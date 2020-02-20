"""Stub generator for C modules.

The public interface is via the mypy.stubgen module.
"""

import importlib
import inspect
import os.path
import re
from typing import List, Dict, Tuple, Optional, Mapping, Any, Set
from types import ModuleType

from mypy.stubutil import (
    is_c_module, write_header, infer_sig_from_docstring,
    infer_prop_type_from_docstring
)


def generate_stub_for_c_module(module_name: str,
                               target: str,
                               add_header: bool = True,
                               sigs: Dict[str, str] = {},
                               class_sigs: Dict[str, str] = {},
                               ) -> None:
    module = importlib.import_module(module_name)
    assert is_c_module(module), '%s is not a C module' % module_name
    subdir = os.path.dirname(target)
    if subdir and not os.path.isdir(subdir):
        os.makedirs(subdir)
    imports = []  # type: List[str]
    functions = []  # type: List[str]
    done = set()
    items = sorted(module.__dict__.items(), key=lambda x: x[0])
    for name, obj in items:
        if is_c_function(obj):
            generate_c_function_stub(module, name, obj, functions, imports=imports, sigs=sigs)
            done.add(name)
    types = []  # type: List[str]
    for name, obj in items:
        if name.startswith('__') and name.endswith('__'):
            continue
        if is_c_type(obj):
            generate_c_type_stub(module, name, obj, types, imports=imports, sigs=sigs,
                                 class_sigs=class_sigs)
            done.add(name)
    variables = []
    for name, obj in items:
        if name.startswith('__') and name.endswith('__'):
            continue
        if name not in done and not inspect.ismodule(obj):
            type_str = type(obj).__name__
            if type_str not in ('int', 'str', 'bytes', 'float', 'bool'):
                type_str = 'Any'
            variables.append('%s: %s' % (name, type_str))
    output = []
    for line in sorted(set(imports)):
        output.append(line)
    for line in variables:
        output.append(line)
    if output and functions:
        output.append('')
    for line in functions:
        output.append(line)
    for line in types:
        if line.startswith('class') and output and output[-1]:
            output.append('')
        output.append(line)
    output = add_typing_import(output)
    with open(target, 'w') as file:
        if add_header:
            write_header(file, module_name)
        for line in output:
            file.write('%s\n' % line)


def add_typing_import(output: List[str]) -> List[str]:
    names = []
    for name in ['Any', 'Union', 'Tuple', 'Optional', 'List', 'Dict']:
        if any(re.search(r'\b%s\b' % name, line) for line in output):
            names.append(name)
    if names:
        return ['from typing import %s' % ', '.join(names), ''] + output
    else:
        return output[:]


def is_c_function(obj: object) -> bool:
    return inspect.isbuiltin(obj) or type(obj) is type(ord)


def is_c_method(obj: object) -> bool:
    return inspect.ismethoddescriptor(obj) or type(obj) in (type(str.index),
                                                            type(str.__add__),
                                                            type(str.__new__))


def is_c_classmethod(obj: object) -> bool:
    return inspect.isbuiltin(obj) or type(obj).__name__ in ('classmethod',
                                                            'classmethod_descriptor')


def is_c_property(obj: object) -> bool:
    return inspect.isdatadescriptor(obj) and hasattr(obj, 'fget')


def is_c_property_readonly(prop: object) -> bool:
    return getattr(prop, 'fset') is None


def is_c_type(obj: object) -> bool:
    return inspect.isclass(obj) or type(obj) is type(int)


def generate_c_function_stub(module: ModuleType,
                             name: str,
                             obj: object,
                             output: List[str],
                             imports: List[str],
                             self_var: Optional[str] = None,
                             sigs: Dict[str, str] = {},
                             class_name: Optional[str] = None,
                             class_sigs: Dict[str, str] = {},
                             ) -> None:
    ret_type = 'None' if name == '__init__' and class_name else 'Any'

    if self_var:
        self_arg = '%s, ' % self_var
    else:
        self_arg = ''
    if (name in ('__new__', '__init__') and name not in sigs and class_name and
            class_name in class_sigs):
        sig = class_sigs[class_name]
    else:
        docstr = getattr(obj, '__doc__', None)
        inferred = infer_sig_from_docstring(docstr, name)
        if inferred:
            sig, ret_type = inferred
        else:
            if class_name and name not in sigs:
                sig = infer_method_sig(name)
            else:
                sig = sigs.get(name, '(*args, **kwargs)')
    # strip away parenthesis
    sig = sig[1:-1]
    if sig:
        if self_var:
            # remove annotation on self from signature if present
            groups = sig.split(',', 1)
            if groups[0] == self_var or groups[0].startswith(self_var + ':'):
                self_arg = ''
                sig = '{},{}'.format(self_var, groups[1]) if len(groups) > 1 else self_var
    else:
        self_arg = self_arg.replace(', ', '')

    if sig:
        sig_types = []
        # convert signature in form of "self: TestClass, arg0: str" to
        # list [[self, TestClass], [arg0, str]]
        for arg in sig.split(','):
            arg_type = arg.split(':', 1)
            if len(arg_type) == 1:
                # there is no type provided in docstring
                sig_types.append(arg_type[0].strip())
            else:
                arg_type_name = strip_or_import(arg_type[1].strip(), module, imports)
                sig_types.append('%s: %s' % (arg_type[0].strip(), arg_type_name))
        sig = ", ".join(sig_types)

    ret_type = strip_or_import(ret_type, module, imports)
    output.append('def %s(%s%s) -> %s: ...' % (name, self_arg, sig, ret_type))


def strip_or_import(typ: str, module: ModuleType, imports: List[str]) -> str:
    """
    Strips unnecessary module names from typ.

    If typ represents a type that is inside module or is a type comming from builtins, remove
    module declaration from it

    :param typ: name of the type
    :param module: in which this type is used
    :param imports: list of import statements. May be modified during the call
    :return: stripped name of the type
    """
    arg_type = typ
    if module and typ.startswith(module.__name__):
        arg_type = typ[len(module.__name__) + 1:]
    elif '.' in typ:
        arg_module = arg_type[:arg_type.rindex('.')]
        if arg_module == 'builtins':
            arg_type = arg_type[len('builtins') + 1:]
        else:
            imports.append('import %s' % (arg_module,))
    return arg_type


def generate_c_property_stub(name: str, obj: object, output: List[str], readonly: bool) -> None:
    docstr = getattr(obj, '__doc__', None)
    inferred = infer_prop_type_from_docstring(docstr)
    if not inferred:
        inferred = 'Any'

    output.append('@property')
    output.append('def {}(self) -> {}: ...'.format(name, inferred))
    if not readonly:
        output.append('@{}.setter'.format(name))
        output.append('def {}(self, val: {}) -> None: ...'.format(name, inferred))


def generate_c_type_stub(module: ModuleType,
                         class_name: str,
                         obj: type,
                         output: List[str],
                         imports: List[str],
                         sigs: Dict[str, str] = {},
                         class_sigs: Dict[str, str] = {},
                         ) -> None:
    # typeshed gives obj.__dict__ the not quite correct type Dict[str, Any]
    # (it could be a mappingproxy!), which makes mypyc mad, so obfuscate it.
    obj_dict = getattr(obj, '__dict__')  # type: Mapping[str, Any]
    items = sorted(obj_dict.items(), key=lambda x: method_name_sort_key(x[0]))
    methods = []  # type: List[str]
    properties = []  # type: List[str]
    done = set()  # type: Set[str]
    for attr, value in items:
        if is_c_method(value) or is_c_classmethod(value):
            done.add(attr)
            if not is_skipped_attribute(attr):
                if is_c_classmethod(value):
                    methods.append('@classmethod')
                    self_var = 'cls'
                else:
                    self_var = 'self'
                if attr == '__new__':
                    # TODO: We should support __new__.
                    if '__init__' in obj_dict:
                        # Avoid duplicate functions if both are present.
                        # But is there any case where .__new__() has a
                        # better signature than __init__() ?
                        continue
                    attr = '__init__'
                generate_c_function_stub(module, attr, value, methods, imports=imports,
                                         self_var=self_var, sigs=sigs, class_name=class_name,
                                         class_sigs=class_sigs)
        elif is_c_property(value):
            done.add(attr)
            generate_c_property_stub(attr, value, properties, is_c_property_readonly(value))

    variables = []
    for attr, value in items:
        if is_skipped_attribute(attr):
            continue
        if attr not in done:
            variables.append('%s: Any = ...' % attr)
    all_bases = obj.mro()
    if all_bases[-1] is object:
        # TODO: Is this always object?
        del all_bases[-1]
    # remove pybind11_object. All classes generated by pybind11 have pybind11_object in their MRO,
    # which only overrides a few functions in object type
    if all_bases and all_bases[-1].__name__ == 'pybind11_object':
        del all_bases[-1]
    # remove the class itself
    all_bases = all_bases[1:]
    # Remove base classes of other bases as redundant.
    bases = []  # type: List[type]
    for base in all_bases:
        if not any(issubclass(b, base) for b in bases):
            bases.append(base)
    if bases:
        bases_str = '(%s)' % ', '.join(
            strip_or_import(
                '%s.%s' % (base.__module__, base.__name__),
                module,
                imports
            ) for base in bases
        )
    else:
        bases_str = ''
    if not methods and not variables and not properties:
        output.append('class %s%s: ...' % (class_name, bases_str))
    else:
        output.append('class %s%s:' % (class_name, bases_str))
        for variable in variables:
            output.append('    %s' % variable)
        for method in methods:
            output.append('    %s' % method)
        for prop in properties:
            output.append('    %s' % prop)


def method_name_sort_key(name: str) -> Tuple[int, str]:
    if name in ('__new__', '__init__'):
        return (0, name)
    if name.startswith('__') and name.endswith('__'):
        return (2, name)
    return (1, name)


def is_skipped_attribute(attr: str) -> bool:
    return attr in ('__getattribute__',
                    '__str__',
                    '__repr__',
                    '__doc__',
                    '__dict__',
                    '__module__',
                    '__weakref__')  # For pickling


def infer_method_sig(name: str) -> str:
    if name.startswith('__') and name.endswith('__'):
        name = name[2:-2]
        if name in ('hash', 'iter', 'next', 'sizeof', 'copy', 'deepcopy', 'reduce', 'getinitargs',
                    'int', 'float', 'trunc', 'complex', 'bool'):
            return '()'
        if name == 'getitem':
            return '(index)'
        if name == 'setitem':
            return '(index, object)'
        if name in ('delattr', 'getattr'):
            return '(name)'
        if name == 'setattr':
            return '(name, value)'
        if name == 'getstate':
            return '()'
        if name == 'setstate':
            return '(state)'
        if name in ('eq', 'ne', 'lt', 'le', 'gt', 'ge',
                    'add', 'radd', 'sub', 'rsub', 'mul', 'rmul',
                    'mod', 'rmod', 'floordiv', 'rfloordiv', 'truediv', 'rtruediv',
                    'divmod', 'rdivmod', 'pow', 'rpow'):
            return '(other)'
        if name in ('neg', 'pos'):
            return '()'
    return '(*args, **kwargs)'
