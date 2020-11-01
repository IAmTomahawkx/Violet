import contextlib
import hashlib
import importlib
import re
import os
import pprint
import sys
import types
import contextlib
import subprocess

from violet.lexer import lexer
from violet.parser import parser
from violet import vast as ast
from violet import errors
from violet.errors import StatementError
from violet import objects
from violet._util import IndexableNamespace

_STD_TYPES = {
	'nil': objects.Void,
	'List': objects.List,
	'String': objects.String,
	'Void': objects.Void,
	'Integer': objects.Integer,
	'Boolean': objects.Boolean
}

_PY_TYPES = {
	int: objects.Integer,
	str: objects.String,
	type(None): objects.Void,
	list: objects.List.from_value0
}

_print = print
def print(*args, **kwargs):
	kwargs['file'] = kwargs.get('file', sys.stderr)
	_print(*args, **kwargs)

class VarNotFound(Exception):
	def __init__(self, var):
		super().__init__(f"variable {var.name!r} is not defined")

class CannotReassignConst(Exception):
	def __init__(self, var):
		super().__init__(f"constant variable {var.name!r} cannot be reassigned")

class Scope:
	def __init__(self, runner):
		self.runner = runner
		self.vars = {}
		self.const_vars = {}
		self.parent = None

		self.hash = hashlib.sha1(os.urandom(16)).hexdigest()
		# print("spawned scope", self.hash)

	def __repr__(self):
		return "Scope(" + repr(self.vars) + ", " + repr(self.const_vars) + ")"

	def is_var_assigned(self, identifier, recurse=True):
		return \
			identifier in self.vars or \
			identifier in self.const_vars or \
			identifier.name in _STD_TYPES or \
			(recurse and self.parent and self.parent.is_var_assigned(identifier))

	def get_var_noid(self, name):
		return self.get_var(ast.Identifier(name, -1))

	def get_var(self, identifier):
		# print(self.hash, ": getting", identifier)
		try:
			if identifier.name in _STD_TYPES:
				return _STD_TYPES[identifier.name]
			var = self.vars.get(identifier)
			if var is None:
				var = self.const_vars.get(identifier)
			if var is None:
				raise VarNotFound(identifier)
			return var
		except VarNotFound:
			if not self.parent:  # self.parent?.getVar(identifier) ?: throw error
				raise
			return self.parent.get_var(identifier)

	def reassign_var(self, identifier, value, *, const=None):  # pointless const argument, just for safety
		# print("reassign", identifier, value)
		if identifier in self.const_vars:
			raise CannotReassignConst(identifier)  # cannot reassign consts
		orig = ast.TypeId(ast.Identifier(self.vars[identifier].__class__.__name__, identifier.lineno))
		orig.type_check(value, self.runner)
		self.vars[identifier] = value

	def set_var(self, identifier, value, *, const=False):
		# print(self.hash, ": assigning", identifier, value)
		if not isinstance(value, (objects.Object, objects.ThinPythonObjectWrapper)):
			value = objects.ThinPythonObjectWrapper(value)
		# print("assigning", identifier, "to", value, "as const?", const)
		if self.is_var_assigned(identifier, False):
			print(f"WARN: shadowing variable {identifier.transform_to_string()!r}")
		if const:
			# print("set const var")
			self.const_vars[identifier] = value
		else:
			# print("set var")
			self.vars[identifier] = value

class Runner:
	def __init__(self, code, *, debug=False, write_ast=False):
		self.debug = debug
		self.global_scope = gl = Scope(self)
		self.scopes = {gl.hash: gl}
		self.active_scope = gl.hash
		self.code = code
		self.lineno = 0
		self.write_ast = write_ast

	@staticmethod
	def wrap_py_type(value):
		return _PY_TYPES[type(value)](value, runner=self)

	def get_var(self, *args, **kwargs):
		return self.get_current_scope().get_var(*args, **kwargs)

	def get_current_scope(self):
		return self.scopes[self.active_scope]

	@classmethod
	def open(cls, fp, **kwargs):
		with open(fp) as f:
			return cls(f.read(), **kwargs)

	def run(self):
		module = ast.Module([])
		body = parser.parse(lexer.tokenize(self.code))
		if parser._error_list:
			for error in parser._error_list:
				print(f"ERROR:{error.lineno}: Unexpected {error.value!r}")
			parser._error_list.clear()
			sys.exit(1)

		module.body.extend(body)
		# print(module, file=sys.stdout)
		try:
			# print(module.body)
			self.exec_module(module)
		except errors.Panic as e:
			print("FATAL: system error occured:", e, file=sys.stderr)
			sys.exit(9)
		except StatementError as e:
			if self.debug:
				raise e
			print(f"ERROR:{e.stmt.lineno}: {e}")
			sys.exit(1)
		# print(self.get_current_scope())
		# pprint.pprint(self.scopes)
		# return self
		if self.write_ast:
			with open("test_out.py", "w") as f:
				print(module, file=f)
			subprocess.run(["black", "test_out.py"], capture_output=True)
		try:
			main = self.get_current_scope().get_var(ast.Identifier('main', -1))
		except VarNotFound:
			print("ERROR: missing entry point function 'main'", file=sys.stderr)
			sys.exit(1)
		argv = ast.Primitive(
			IndexableNamespace(
				value=[
					ast.Primitive(
						IndexableNamespace(
							value='"a"',
							lineno=main.lineno
						),
						objects.String
					)
				],
				lineno=main.lineno
			),
			objects.List
		).eval(self)

		try:
			with self.new_scope():
				main([argv], runner=self)
		except (StatementError) as e:
			if self.debug:
				raise e
			print(f"ERROR:{e.stmt.lineno}:", e, file=sys.stderr)
			sys.exit(1)
		except Exception as e:
			if self.debug:
				raise e
			print(f"ERROR:{main.lineno}:", e, file=sys.stderr)
			sys.exit(1)

		# pprint.pprint(self.scopes)

	@contextlib.contextmanager
	def new_scope(self):
		scope = Scope(self)
		scope.parent = self.get_current_scope()
		self.scopes[scope.hash] = scope
		old_scope = self.active_scope
		# print("opening scope", scope.hash, "with parent", old_scope)
		self.active_scope = scope.hash
		try:
			yield
		finally:
			# print("closing scope", scope.hash, "to", old_scope)
			self.active_scope = old_scope

	def exec_module_body(self, stmt_list):
		for statement in stmt_list:
			# print("executing", statement.__class__.__name__, getattr(statement, 'name', None))

			try:
				if isinstance(statement, ast.Import):
					self._exec_import(statement)
				elif isinstance(statement, ast.Assignment):
					self._exec_assignment(statement)
				elif isinstance(statement, ast.Reassignment):
					self._exec_assignment(statement, True)
				elif isinstance(statement, ast.Function):
					self._exec_function_spawn(statement)
				else:
					raise StatementError(statement, f'unexpected {statement.__class__.__name__!r} statement')
			except Exception as e:
				if isinstance(e, StatementError):
					raise
				raise StatementError(statement, str(e))

	def exec_function_body(self, body, func):
		for statement in body:
			# print("executing", statement.__class__.__name__, getattr(statement, 'name', None))
			if func._return_flag:
				break
			self.lineno += 1
			# print("executing", statement.__class__.__name__)
			if isinstance(statement, ast.Assignment):
				self._exec_assignment(statement)
			elif isinstance(statement, ast.Reassignment):
				self._exec_assignment(statement, True)
			elif isinstance(statement, ast.Return):
				self._exec_return(statement, func)
				func._return_flag = True
			elif isinstance(statement, ast.FunctionCall):
				statement.eval(self)
			elif isinstance(statement, ast.Control):
				statement.eval(self, func)
			else:
				raise StatementError(statement, f'unexpected {statement.__class__.__name__!r} statement')

	def exec_module(self, module):
		self.exec_module_body(module.body)

	def _exec_import(self, stmt):
		form = stmt.from_module
		if isinstance(form, ast.Attribute):
			# print(form)
			if form.name.name == 'std':
				return self._exec_std_import(stmt)
		else:
			if form.name == 'std':
				return self._exec_std_import(stmt)
			return self._exec_local_import(stmt)

	def _exec_std_import(self, stmt):
		# print(stmt)
		form = stmt.from_module
		name = form.transform_to_string()

		try:
			# print("importing violet."+name)
			module = importlib.import_module('violet.'+name)
		except ImportError:
			raise StatementError(stmt, f'module {name!r} does not exist')
		else:
			for iport in stmt.importing:
				if not hasattr(module, iport.name):
					raise StatementError(stmt, f'failed to import {iport.name!r} from {name!r}')
				else:
					# print("imported", iport)
					self.get_current_scope().set_var(iport, getattr(module, iport.name))
		# print(self.get_current_scope())

	def _exec_local_import(self, stmt):
		try:
			new = Runner.open(stmt.from_module.name+'.vi').run()
		except FileNotFoundError:
			raise StatementError(stmt, f'module {stmt.from_module.name!r} does not exist')

		for name in stmt.importing:
			try:
				var = new.get_current_scope().get_var(name)
			except Exception as e:
				raise StatementError(stmt, f'failed to import {name.name!r} from {stmt.from_module.name!r}')
			else:
				self.get_current_scope().set_var(name, var)

	def _exec_assignment(self, statement, reassign=False):
		# print(statement, reassign)
		scope = self.global_scope if not reassign and statement.global_scope else self.get_current_scope()
		try:
			expr = statement.expression.eval(self)
		except Exception as e:
			raise StatementError(statement, str(e))
		# print(expr)
		if reassign:
			if not scope.is_var_assigned(statement.identifier):
				raise StatementError(statement, f'variable {statement.identifier.name!r} is not defined')
			meth = scope.reassign_var
		else:
			typ = statement.type
			if typ is not None:
				typ.type_check(expr, self)

			meth = scope.set_var

		try:
			# print(meth)
			meth(statement.identifier, expr, const=statement.constant)
		except Exception as e:
			raise StatementError(statement, str(e))

	def _exec_return(self, statement, func):
		# print(statement)
		expr = statement.expr
		if expr is None:
			ret = Void()
		else:
			ret = expr.eval(self)
		if func.return_type is None:
			if not isinstance(ret, objects.Object):
				ret = self.wrap_py_type(ret)
			func.return_type = ret.get_type()
		func.return_type.type_check(ret, self)
		func._return = ret

	def _exec_function_spawn(self, stmt):
		# print("spawn function")
		self.get_current_scope().set_var(stmt.name, stmt.eval(self))
	