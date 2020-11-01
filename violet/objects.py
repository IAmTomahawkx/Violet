import inspect
from violet.errors import *
from violet._util import IndexableNamespace

# __all__ = ['Module']

_OPS = {
	'plus': '+',
	'minus': '-',
	'times': '*',
	'divide': '/',
	'modulus': '%',
	'call': '()',
	'equals': '==',
	'not_equals': '!=',
	'greater': '>',
	'greater_equal': '>=',
	'less': '<',
	'less_equal': '<='
}

class _Meta(type):
	def __new__(mcs, cname, bases, attrs):
		new = attrs.copy()
		for name, value in attrs.items():
			if name.startswith('_operator_'):
				new.pop(name)
				op = name[10:]
				name = 'operator' + _OPS[op]
				# print(cname, name)
				new[name] = value
		return super().__new__(mcs, cname, bases, new)

	def __repr__(cls):
		# return cls.__module__ + '.' + cls.__name__
		return cls.__name__

class Object(metaclass=_Meta):
	def ensure_type(self, other):
		return isinstance(other, self.__class__)

	def __add__(self, other):
		return self.get_special_method('+')(other)

	def __sub__(self, other):
		return self.get_special_method('-')(other)

	def __mul__(self, other):
		return self.get_special_method('*')(other)

	def __floordiv__(self, other):
		return self.get_special_method('/')(other)

	def __mod__(self, other):
		return self.get_special_method('%')(other)

	def get_special_method(self, name):
		# print(name)
		# sprint(dir(self))
		meth = getattr(self, 'operator'+name, None)
		if meth is None:
			raise Exception(f'operator{name} not available on type {self.__class__.__name__!r}')
		if not inspect.ismethod(meth):
			raise Panic(f"operator{name} not defined as a callable method")
		return meth

	def __call__(self, args, *, runner):
		return self.get_special_method('()')(args, runner=runner)

	def __eq__(self, other):
		return self.get_special_method('==')(other)

	def __ne__(self, other):
		return self.get_special_method('!=')(other)

	def __gt__(self, other):
		return self.get_special_method('>')(other)

	def __ge__(self, other):
		return self.get_special_method('>=')(other)

	def __lt__(self, other):
		return self.get_special_method('<')(other)

	def __le__(self, other):
		return self.get_special_method('<=')(other)

class ThinPythonObjectWrapper:
	def __init__(self, obj):
		self.obj = obj

	def __repr__(self):
		return 'PyObject_'+self.obj.__class__.__name__

	def __getattr__(self, name):
		if name == '__repr__':
			return super().__getattribute__(name)
		return self.obj.__getattribute__(name)

	def __call__(self, *args, **kwargs):
		return self.obj.__call__(*args, **kwargs)

	def __str__(self):
		return str(self.obj)

class Module(Object):
	def __init__(self, module):
		self.module = module

	def __repr__(self):
		return f'Module({self.module!r})'

	def __str__(self):
		return repr(self)

	def __getattr__(self, name):
		try:
			return self.module.__getattr__(name)
		except AttributeError:
			raise HasNoAttribute(self, name)

class Primitive(Object):
	def __init__(self, value):
		self.value0 = value

	def __repr__(self):
		return f'{self.__class__.__name__}({self.value0!r})'

	def __str__(self):
		return repr(self.value0) if self.value0 is not None else 'nil'

	def get_type(self):
		from violet.vast import TypeId, Identifier
		return TypeId(Identifier(self.__class__.__name__, -1))

	def _operator_equals(self, other):
		return Boolean(self.value0 == other.value0)

	def _operator_not_equals(self, other):
		return Boolean(self.value0 != other.value0)

	def _operator_greater(self, other):
		return Boolean(self.value0 > other.value0)

	def _operator_greater_equal(self, other):
		return Boolean(self.value0 >= other.value0)

	def _operator_less(self, other):
		return Boolean(self.value0 < other.value0)

	def _operator_less_equal(self, other):
		return Boolean(self.value0 <= other.value0)

class Void(Primitive):
	def __init__(self):
		super().__init__(None)

	def __eq__(self, other):
		return isinstance(other, Void)

	def get_type(self):
		from violet.vast import TypeId, Identifier
		return TypeId(Identifier('Void', -1))

class Boolean(Primitive):
	def __bool__(self):
		return self.value0

	def __repr__(self):
		return repr(self.value0).lower()

class String(Primitive):
	@classmethod
	def new(cls, value, *, runner):
		if len(value) > 1:
			raise Exception("too many arguments for function call (expected 1 argument)")
		return cls(str(value[0]))

class Integer(Primitive):
	def _operator_plus(self, other):
		if not self.ensure_type(other):
			raise Exception(f'operator+ not applicable between types {self.__class__.__name__!r} and {other.__class__.__name__!r}')
		return self.__class__(self.value0 + other.value0)

	def _operator_minus(self, other):
		if not self.ensure_type(other):
			raise Exception(f'operator- not applicable between types {self.__class__.__name__!r} and {other.__class__.__name__!r}')
		return self.__class__(self.value0 - other.value0)

	def _operator_times(self, other):
		if not self.ensure_type(other):
			raise Exception(f'operator* not applicable between types {self.__class__.__name__!r} and {other.__class__.__name__!r}')
		return self.__class__(self.value0 * self.value0)

	def _operator_divide(self, other):
		if not self.ensure_type(other):
			raise Exception(f'operator/ not applicable between types {self.__class__.__name__!r} and {other.__class__.__name__!r}')
		return self.__class__(self.value0 // other.value0)

	def _operator_modulus(self, other):
		if not self.ensure_type(other):
			raise Exception(f'operator/ not applicable between types {self.__class__.__name__!r} and {other.__class__.__name__!r}')
		return self.__class__(self.value0 % other.value0)

class List(Primitive):
	@classmethod
	def from_value0(cls, value, *, runner):
		if not value:  # empty expr list?
			raise Exception("cannot infer type of empty list")
		initial = value[0].eval(runner).__class__
		values = []
		for arg in value:
			arg = arg.eval(runner)
			if not isinstance(arg, initial):
				raise Exception(f"multi-typed lists are invalid (found {arg.__class__.__name__!r}, expected {initial.__name__!r})")
			values.append(arg)
		return cls(values)

class Function(Object):
	# _attrs = ('name', 'params', 'return_type', 'body')

	def __repr__(self):
		return f"Function<{self.name}>()"

	def __init__(self, name, params, return_type, body, lineno):
		from violet.vast import Return, Primitive

		self.lineno = lineno
		self.name = name
		self.params = params
		self.return_type = return_type
		self.body = body
		self._return = None
		self._return_flag = False

		if body:
			# print(body[-1])
			if not isinstance(body[-1], Return):
				body.append(Return(Primitive(IndexableNamespace(value='nil', lineno=body[-1].lineno), Void)))

	def reset_state(self):
		ret = self._return
		self._return = None
		self._return_flag = None
		return ret

	def _operator_call(self, args, *, runner):
		# print(self)
		with runner.new_scope():
			if len(args) < len(self.params):
				raise Exception("not enough arguments for function call")
			params = iter(self.params)
			for value in args:
				try:
					param = next(params)
				except StopIteration:
					raise Exception("too many arguments for function call") from None
				param.type.type_check(value, runner)
				
				runner.get_current_scope().set_var(param.name, value)
			runner.exec_function_body(self.body, self)
			# print("returning type", self._return)
			return self.reset_state()
