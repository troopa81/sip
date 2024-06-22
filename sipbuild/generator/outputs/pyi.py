# SPDX-License-Identifier: BSD-2-Clause

# Copyright (c) 2024 Phil Thompson <phil@riverbankcomputing.com>


from ...version import SIP_VERSION_STR

from ..python_slots import is_number_slot, reflected_slot
from ..specification import (AccessSpecifier, ArgumentType, ArrayArgument,
        EnumBaseType, IfaceFileType, PyQtMethodSpecifier, PySlot, Signature)
from ..utils import append_iface_file, find_method

from .formatters import (fmt_argument_as_type_hint, fmt_class_as_type_hint,
        fmt_copying, fmt_scoped_py_name, fmt_signature_as_type_hint)


def output_pyi(spec, project, pyi_filename):
    """ Output a .pyi file. """

    with open(pyi_filename, 'w', encoding='UTF-8') as pf:
        # Write the header.
        version_info_s = f'#\n# Generated by SIP {SIP_VERSION_STR}\n' if project.version_info else ''

        copying_s = fmt_copying(spec.module.copying, '#')

        pf.write(
f'''# The PEP 484 type hints stub file for the {spec.module.py_name} module.
{version_info_s}{copying_s}''')

        if spec.is_composite:
            _composite_module(pf, spec)
        else:
            _module(pf, spec)


def _composite_module(pf, spec):
    """ Output the type hints for a composite module. """

    for mod in spec.module.all_imports:
        if mod.composite is spec.module:
            pf.write(f'from {mod.fq_py_name.name} import *\n')


def _module(pf, spec):
    """ Output the type hints for an ordinary module. """

    module = spec.module

    first = True

    # Generate the imports. Note that we assume the super-types are the
    # standard SIP ones.
    if spec.abi_version >= (13, 0):
        for enum in spec.enums:
            if enum.module is spec.module:
                first = _separate(pf, first=first)
                pf.write('import enum\n')
                break

    if spec.sip_module:
        first = _separate(pf, first=first)
        pf.write(
f'''import typing

import {spec.sip_module}
''')

    imports = []

    for mod in module.all_imports:
        parts = mod.fq_py_name.name.split('.')

        if mod.fq_py_name.name == mod.py_name:
            imports.append('import ' + mod.py_name)
        else:
            scope = mod.fq_py_name.name[:-len(mod.py_name) - 1]
            imports.append('from ' + scope + ' import ' + mod.py_name)

    if imports:
        first = _separate(pf, first=first, minimum=1)
        pf.write('\n'.join(imports) + '\n')

    # Generate any exported type hint code and any module-specific type hint
    # code.
    first = _type_hint_code(pf, spec.exported_type_hint_code, first)
    first = _type_hint_code(pf, module.type_hint_code, first)

    # Generate the types - global enums must be first.
    _enums(pf, spec)

    # The list of enums and classes that have been defined at any particular
    # point so we know if they can be referenced directly rather than by their
    # names as a string.
    defined = []

    for klass in spec.classes:
        if klass.iface_file.module is not module:
            continue

        if klass.external:
            continue

        if klass.no_type_hint:
            continue

        # Only handle non-nested classes here.
        if klass.scope is not None:
            continue

        # We can't handle extenders.
        if klass.real_class is not None:
            continue

        _class(pf, spec, klass, defined)

    for mapped_type in spec.mapped_types:
        if mapped_type.iface_file.module is not module:
            continue

        if mapped_type.py_name is not None:
            _mapped_type(pf, spec, mapped_type, defined)

    _variables(pf, spec, defined)

    first = True

    for member in module.global_functions:
        if member.py_slot is None:
            first = _separate(pf, first=first)
            _callable(pf, spec, member, module.overloads, defined)


def _type_hint_code(pf, type_hint_code, first=True, indent=0):
    """ Output handwritten type hint code. """

    s = ''

    for block in type_hint_code:
        if s == '':
            first = _separate(pf, first=first, indent=indent, minimum=1)
        else:
            s += '\n'

        need_indent = True

        for ch in block.text:
            if need_indent:
                s += _indent(indent)
                need_indent = False

            s += ch

            if ch == '\n':
                need_indent = True

    pf.write(s)

    return first


def _class(pf, spec, klass, defined, indent=0):
    """ Output the type hints for a class. """

    nr_overloads = 0

    if not klass.is_hidden_namespace:
        _separate(pf, indent=indent)

        s = _indent(indent)

        s += f'class {klass.py_name.name}('

        if klass.superclasses:
            s += ', '.join(
                    [fmt_class_as_type_hint(spec, sc, defined)
                            for sc in klass.superclasses])

        elif klass.supertype is not None:
            # In ABI v12 the default supertype does not contain the fully
            # qualified name of the sip module so we fix it here.
            if spec.abi_version[0] == 12 and spec.sip_module and klass.supertype.name.startswith('sip.'):
                s += spec.sip_module + klass.supertype.name[4:]
            else:
                s += klass.supertype.name

        else:
            simple = 'simple' if klass.iface_file.type is IfaceFileType.NAMESPACE else ''

            s += f'{_sip_module_name(spec)}{simple}wrapper'

        # See if there is anything in the class body.
        for ctor in klass.ctors:
            if ctor.access_specifier is AccessSpecifier.PRIVATE:
                continue

            if ctor.no_type_hint:
                continue

            nr_overloads += 1

        no_body = (klass.type_hint_code is None and nr_overloads == 0)

        if no_body:
            for overload in klass.overloads:
                if overload.access_specifier is AccessSpecifier.PRIVATE:
                    continue

                if overload.no_type_hint:
                    continue

                no_body = False
                break

        if no_body:
            for enum in spec.enums:
                if enum.scope is klass and not enum.no_type_hint:
                    no_body = False
                    break

        if no_body:
            for nested in spec.classes:
                if nested.scope is klass and not nested.no_type_hint:
                    no_body = False
                    break

        if no_body:
            for variable in spec.variables:
                if variable.scope is klass and not variable.no_type_hint:
                    no_body = False
                    break

        suffix = ' ...' if no_body else ''

        s += f'):{suffix}\n'

        pf.write(s)

        indent += 1

        if klass.type_hint_code is not None:
            _type_hint_code(pf, [klass.type_hint_code], indent=indent)

    _enums(pf, spec, defined=defined, scope=klass, indent=indent)

    # Handle any nested classes.
    for nested in spec.classes:
        if nested.scope is klass and not nested.no_type_hint:
            _class(pf, spec, nested, defined, indent=indent)

    _variables(pf, spec, defined, scope=klass, indent=indent)

    first = True

    for ctor in klass.ctors:
        if ctor.access_specifier is AccessSpecifier.PRIVATE:
            continue

        if ctor.no_type_hint:
            continue

        first = _separate(pf, first=first, indent=indent)

        _ctor(pf, spec, ctor, nr_overloads > 1, defined, indent)

    first = True

    for member in klass.members:
        first = _separate(pf, first=first, indent=indent)

        _callable(pf, spec, member, klass.overloads, defined,
                is_method=not klass.is_hidden_namespace, indent=indent)

    for prop in klass.properties:
        first = _separate(pf, first=first, indent=indent)

        getter = find_method(klass, prop.getter)
        if getter is not None:
            _property(pf, spec, prop, False, getter, klass.overloads, defined,
                    indent)

            if prop.setter is not None:
                setter = find_method(klass, prop.setter)
                if setter is not None:
                    _property(pf, spec, prop, True, setter, klass.overloads,
                            defined, indent)

    if not klass.is_hidden_namespace:
        # Keep track of what has been defined so that forward references are no
        # longer required.
        append_iface_file(defined, klass.iface_file)


def _mapped_type(pf, spec, mapped_type, defined):
    """ Output the type hints for a mapped type. """

    # See if there is anything in the mapped type body.
    no_body = (len(mapped_type.members) == 0)

    if no_body:
        for enum in spec.enums:
            if enum.scope is mapped_type and not enum.no_type_hint:
                no_body = FALSE
                break

    if not no_body:
        _separate(pf)
        pf.write(f'class {mapped_type.py_name.name}({_sip_module_name(spec)}wrapper):\n')

        _enums(pf, spec, defined=defined, scope=mapped_type, indent=1)

        first = True

        for member in mapped_type.members:
            first = _separate(pf, first=first, indent=1)
            _callable(pf, spec, member, member.overloads, defined,
                    is_method=True, indent=1)

    # Keep track of what has been defined so that forward references are no
    # longer required.
    append_iface_file(defined, mapped_type.iface_file)


def _ctor(pf, spec, ctor, overloaded, defined, indent):
    """ Output a ctor type hint. """

    if overloaded:
        s = _indent(indent)
        s += '@typing.overload\n'
        pf.write(s)

    s = _indent(indent)
    s += 'def __init__'
    s += _python_signature(spec, ctor.py_signature, defined)
    s += ': ...\n'

    pf.write(s)


def _enums(pf, spec, defined=None, scope=None, indent=0):
    """ Output the type hints for all the enums in a scope. """

    for enum in spec.enums:
        if enum.module is not spec.module:
            continue

        if enum.scope is not scope:
            continue

        if enum.no_type_hint:
            continue

        _separate(pf, indent=indent)

        if enum.py_name is not None:
            enum_type = fmt_scoped_py_name(enum.scope, enum.py_name.name)

            superclass = 'int'

            if spec.abi_version >= (13, 0):
                if enum.base_type is EnumBaseType.ENUM:
                    superclass = 'enum.Enum'
                elif enum.base_type is EnumBaseType.FLAG:
                    superclass = 'enum.Flag'
                elif enum.base_type in (EnumBaseType.INT_ENUM, EnumBaseType.UINT_ENUM):
                    superclass = 'enum.IntEnum'
                elif enum.base_type is EnumBaseType.INT_FLAG:
                    superclass = 'enum.IntFlag'

            # Handle an enum with no members.
            for member in enum.members:
                if not member.no_type_hint:
                    trivial = ''
                    break
            else:
                trivial = ' ...'

            s = _indent(indent)
            s+= f'class {enum.py_name.name}({superclass}):{trivial}\n'
            pf.write(s)

            indent += 1
        else:
            enum_type = 'int'

        for member in enum.members:
            if not member.no_type_hint:
                s = _indent(indent)
                s += f'{member.py_name.name} = ... # type: {enum_type}\n'
                pf.write(s)

        if enum.py_name is not None:
            indent -= 1


def _variables(pf, spec, defined, scope=None, indent=0):
    """ Output the type hints for all the variables in a scope. """

    first = True

    for variable in spec.variables:
        if variable.module is not spec.module:
            continue

        if variable.scope is not scope:
            continue

        if variable.no_type_hint:
            continue

        py_type = fmt_argument_as_type_hint(spec, variable.type, defined,
                arg_nr=None)

        first = _separate(pf, first=first, indent=indent)

        s = _indent(indent)
        s += f'{variable.py_name.name} = ... # type: {py_type}\n'
        pf.write(s)


def _callable(pf, spec, member, overloads, defined, is_method=False, indent=0):
    """ Output the type hints for a callable. """

    # Get the non-reflected and reflected overloads.
    nonreflected_overloads = []
    reflected_overloads = []

    for overload in overloads:
        if overload.access_specifier is AccessSpecifier.PRIVATE:
            continue

        if overload.common is not member:
            continue

        if overload.no_type_hint:
            continue

        # Signals can have the same name as ordinary methods however
        # 'typing.overload' cannot be used with ClassVar.  We choose to
        # generate a type hint for the signal rather than any method.
        if overload.pyqt_method_specifier is PyQtMethodSpecifier.SIGNAL:
            scope = '' if spec.module.py_name == 'QtCore' else 'QtCore.'

            s = _indent(indent)
            s += f'{overload.common.py_name.name}: typing.ClassVar[{scope}pyqtSignal]\n'
            pf.write(s)

            return

        if is_number_slot(overload.common.py_slot) and overload.is_reflected:
            reflected_overloads.append(overload)
        else:
            nonreflected_overloads.append(overload)

    # Handle each non-reflected overload.
    overloaded = len(nonreflected_overloads) > 1
    first_overload = True

    for overload in nonreflected_overloads:
        _overload(pf, spec, overload, overloaded, first_overload, is_method,
                defined, indent)
        first_overload = False

    # Handle each reflected overload.
    overloaded = len(reflected_overloads) > 1
    first_overload = True

    for overload in reflected_overloads:
        _overload(pf, spec, overload, overloaded, first_overload, is_method,
                defined, indent)
        first_overload = False


def _property(pf, spec, prop, is_setter, member, overloads, defined, indent):
    """ Output the type hints for a property. """

    for overload in overloads:
        if overload.access_specifier is AccessSpecifier.PRIVATE:
            continue

        if overload.common is not member:
            continue

        if overload.no_type_hint:
            continue

        s = _indent(indent)

        if is_setter:
            s += f'@{prop.name.name}.setter\n'
        else:
            s += '@property\n'

        pf.write(s)

        signature = _python_signature(spec, overload.py_signature, defined)

        s = _indent(indent)
        s += f'def {prop.name.name}{signature}: ...\n'
        pf.write(s)

        break


def _overload(pf, spec, overload, overloaded, first_overload, is_method,
        defined, indent):
    """ Output the type hints for a single overload. """

    # mypy recommends using 'object' as the argument type.
    is_eq_slot = (overload.common.py_slot in (PySlot.EQ, PySlot.NE))

    # The recommendation means any subsequent overloads are pointless.
    if is_eq_slot:
        if not first_overload:
            return
    elif overloaded:
        pf.write(_indent(indent) + '@typing.overload\n')

    if is_method and overload.is_static:
        pf.write(_indent(indent) + '@staticmethod\n')

    py_name = overload.common.py_name.name
    py_signature = overload.py_signature

    s = _indent(indent)

    if is_eq_slot:
        signature = '(self, other: object)'
    else:
        need_self = (is_method and not overload.is_static)

        if is_number_slot(overload.common.py_slot):
            # Use the reflected name if appropriate.
            if overload.is_reflected:
                py_name = reflected_slot(overload.common.py_slot)

            # A global slot will still have both arguments so pick the relevant
            # one.
            if len(py_signature.args) > 1:
                if overload.is_reflected:
                    arg = py_signature.args[0]
                else:
                    arg = py_signature.args[1]

                py_signature = Signature(args=[arg],
                        result=py_signature.result)

        signature = _python_signature(spec, py_signature, defined,
                need_self=need_self)

    s += f'def {py_name}{signature}: ...\n'

    pf.write(s)


def _python_signature(spec, signature, defined, need_self=True):
    """ Return a Python signature. """

    return fmt_signature_as_type_hint(spec, signature, need_self=need_self,
            defined=defined)


def _indent(indent):
    """ Return the required indentation. """

    return ' ' * (4 * indent)


def _separate(pf, first=True, indent=0, minimum=None):
    """ Output a newline if not already done. """

    if first:
        pf.write('\n' if indent else '\n\n')
    elif minimum is not None:
        pf.write('\n' * minimum)

    return False


def _sip_module_name(spec):
    """ Return the name of the sip module to be used as a prefix to an object
    in the module.
    """

    return spec.sip_module + '.' if spec.sip_module else ''