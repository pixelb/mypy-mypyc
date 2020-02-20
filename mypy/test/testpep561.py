from contextlib import contextmanager
from enum import Enum
import os
import sys
import tempfile
from typing import Tuple, List, Generator, Optional
from unittest import TestCase, main

import mypy.api
from mypy.modulefinder import get_site_packages_dirs
from mypy.test.config import package_path
from mypy.test.helpers import run_command
from mypy.util import try_find_python2_interpreter

# NOTE: options.use_builtins_fixtures should not be set in these
# tests, otherwise mypy will ignore installed third-party packages.

SIMPLE_PROGRAM = """
from typedpkg.sample import ex
from typedpkg import dne
a = ex([''])
reveal_type(a)
"""

_NAMESPACE_PROGRAM = """
{import_style}
from typedpkg_ns.ns.dne import dne

af("abc")
bf(False)
dne(123)

af(False)
bf(2)
dne("abc")
"""


class NSImportStyle(Enum):
    # These should all be on exactly two lines because NamespaceMsg
    # uses line numbers which expect the imports to be exactly two lines
    from_import = """\
from typedpkg.pkg.aaa import af
from typedpkg_ns.ns.bbb import bf"""
    import_as = """\
import typedpkg.pkg.aaa as nm; af = nm.af
import typedpkg_ns.ns.bbb as am; bf = am.bf"""
    reg_import = """\
import typedpkg.pkg.aaa; af = typedpkg.pkg.aaa.af
import typedpkg_ns.ns.bbb; bf = typedpkg_ns.ns.bbb.bf"""


class SimpleMsg(Enum):
    msg_dne = "{tempfile}:3: error: Module 'typedpkg' has no attribute 'dne'"
    msg_list = "{tempfile}:5: error: Revealed type is 'builtins.list[builtins.str]'"
    msg_tuple = "{tempfile}:5: error: Revealed type is 'builtins.tuple[builtins.str]'"


class NamespaceMsg(Enum):
    cfm_beta = ("{tempfile}:4: error: Cannot find module named "
                "'typedpkg_ns.ns.dne'")
    help_note = ('{tempfile}:4: note: See https://mypy.readthedocs.io/en/latest/'
                 'running_mypy.html#missing-imports')
    bool_str = ('{tempfile}:10: error: Argument 1 has incompatible type '
                '"bool"; expected "str"')
    int_bool = ('{tempfile}:11: error: Argument 1 has incompatible type '
                '"int"; expected "bool"')
    to_bool_str = ('{tempfile}:10: error: Argument 1 to "af" has incompatible type '
                   '"bool"; expected "str"')
    to_int_bool = ('{tempfile}:11: error: Argument 1 to "bf" has incompatible type '
                   '"int"; expected "bool"')


def create_ns_program_src(import_style: NSImportStyle) -> str:
    return _NAMESPACE_PROGRAM.format(import_style=import_style.value)


class ExampleProg(object):
    _fname = 'test_program.py'

    def __init__(self, source_code: str) -> None:
        self._source_code = source_code

        self._temp_dir = None  # type: Optional[tempfile.TemporaryDirectory[str]]
        self._full_fname = ''

    def create(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._full_fname = os.path.join(self._temp_dir.name, self._fname)
        with open(self._full_fname, 'w+', encoding='utf8') as f:
            f.write(self._source_code)

    def cleanup(self) -> None:
        if self._temp_dir:
            self._temp_dir.cleanup()

    def build_msg(self, *msgs: Enum) -> str:
        return '\n'.join(
            msg.value.format(tempfile=self._full_fname)
            for msg in msgs
        ) + '\n'

    def check_mypy_run(self,
                       python_executable: str,
                       expected_out: List[Enum],
                       expected_err: str = '',
                       expected_returncode: int = 1,
                       venv_dir: Optional[str] = None) -> None:
        """Helper to run mypy and check the output."""
        cmd_line = [self._full_fname]
        if venv_dir is not None:
            old_dir = os.getcwd()
            os.chdir(venv_dir)
        try:
            if python_executable != sys.executable:
                cmd_line.append('--python-executable={}'.format(python_executable))
            out, err, returncode = mypy.api.run(cmd_line)
            assert out == self.build_msg(*expected_out), err
            assert err == expected_err, out
            assert returncode == expected_returncode, returncode
        finally:
            if venv_dir is not None:
                os.chdir(old_dir)


class TestPEP561(TestCase):

    @contextmanager
    def virtualenv(self,
                   python_executable: str = sys.executable
                   ) -> Generator[Tuple[str, str], None, None]:
        """Context manager that creates a virtualenv in a temporary directory

        returns the path to the created Python executable"""
        # Sadly, we need virtualenv, as the Python 3 venv module does not support creating a venv
        # for Python 2, and Python 2 does not have its own venv.
        with tempfile.TemporaryDirectory() as venv_dir:
            returncode, lines = run_command([sys.executable,
                                             '-m',
                                             'virtualenv',
                                             '-p{}'.format(python_executable),
                                            venv_dir], cwd=os.getcwd())
            if returncode != 0:
                err = '\n'.join(lines)
                self.fail("Failed to create venv. Do you have virtualenv installed?\n" + err)
            if sys.platform == 'win32':
                yield venv_dir, os.path.abspath(os.path.join(venv_dir, 'Scripts', 'python'))
            else:
                yield venv_dir, os.path.abspath(os.path.join(venv_dir, 'bin', 'python'))

    def install_package(self, pkg: str,
                        python_executable: str = sys.executable,
                        use_pip: bool = True,
                        editable: bool = False) -> None:
        """Context manager to temporarily install a package from test-data/packages/pkg/"""
        working_dir = os.path.join(package_path, pkg)
        if use_pip:
            install_cmd = [python_executable, '-m', 'pip', 'install']
            if editable:
                install_cmd.append('-e')
            install_cmd.append('.')
        else:
            install_cmd = [python_executable, 'setup.py']
            if editable:
                install_cmd.append('develop')
            else:
                install_cmd.append('install')
        returncode, lines = run_command(install_cmd, cwd=working_dir)
        if returncode != 0:
            self.fail('\n'.join(lines))

    def setUp(self) -> None:
        self.simple_prog = ExampleProg(SIMPLE_PROGRAM)
        self.from_ns_prog = ExampleProg(create_ns_program_src(NSImportStyle.from_import))
        self.import_as_ns_prog = ExampleProg(create_ns_program_src(NSImportStyle.import_as))
        self.regular_import_ns_prog = ExampleProg(create_ns_program_src(NSImportStyle.reg_import))

    def tearDown(self) -> None:
        self.simple_prog.cleanup()
        self.from_ns_prog.cleanup()
        self.import_as_ns_prog.cleanup()
        self.regular_import_ns_prog.cleanup()

    def test_get_pkg_dirs(self) -> None:
        """Check that get_package_dirs works."""
        dirs = get_site_packages_dirs(sys.executable)
        assert dirs

    def test_typedpkg_stub_package(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg-stubs', python_executable)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_dne, SimpleMsg.msg_list],
                venv_dir=venv_dir,
            )

    def test_typedpkg(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_tuple],
                venv_dir=venv_dir,
            )

    def test_stub_and_typed_pkg(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable)
            self.install_package('typedpkg-stubs', python_executable)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_list],
                venv_dir=venv_dir,
            )

    def test_typedpkg_stubs_python2(self) -> None:
        self.simple_prog.create()
        python2 = try_find_python2_interpreter()
        if python2:
            with self.virtualenv(python2) as venv:
                venv_dir, py2 = venv
                self.install_package('typedpkg-stubs', py2)
                self.simple_prog.check_mypy_run(
                    py2,
                    [SimpleMsg.msg_dne, SimpleMsg.msg_list],
                    venv_dir=venv_dir,
                )

    def test_typedpkg_python2(self) -> None:
        self.simple_prog.create()
        python2 = try_find_python2_interpreter()
        if python2:
            with self.virtualenv(python2) as venv:
                venv_dir, py2 = venv
                self.install_package('typedpkg', py2)
                self.simple_prog.check_mypy_run(
                    py2,
                    [SimpleMsg.msg_tuple],
                    venv_dir=venv_dir,
                )

    def test_typedpkg_egg(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable, use_pip=False)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_tuple],
                venv_dir=venv_dir,
            )

    def test_typedpkg_editable(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable, editable=True)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_tuple],
                venv_dir=venv_dir,
            )

    def test_typedpkg_egg_editable(self) -> None:
        self.simple_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable, use_pip=False, editable=True)
            self.simple_prog.check_mypy_run(
                python_executable,
                [SimpleMsg.msg_tuple],
                venv_dir=venv_dir,
            )

    def test_nested_and_namespace_from_import(self) -> None:
        self.from_ns_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable)
            self.install_package('typedpkg_ns', python_executable)
            self.from_ns_prog.check_mypy_run(
                python_executable,
                [NamespaceMsg.cfm_beta,
                 NamespaceMsg.help_note,
                 NamespaceMsg.to_bool_str,
                 NamespaceMsg.to_int_bool],
                venv_dir=venv_dir,
            )

    def test_nested_and_namespace_import_as(self) -> None:
        self.import_as_ns_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable)
            self.install_package('typedpkg_ns', python_executable)
            self.import_as_ns_prog.check_mypy_run(
                python_executable,
                [NamespaceMsg.cfm_beta,
                 NamespaceMsg.help_note,
                 NamespaceMsg.bool_str,
                 NamespaceMsg.int_bool],
                venv_dir=venv_dir,
            )

    def test_nested_and_namespace_regular_import(self) -> None:
        self.regular_import_ns_prog.create()
        with self.virtualenv() as venv:
            venv_dir, python_executable = venv
            self.install_package('typedpkg', python_executable)
            self.install_package('typedpkg_ns', python_executable)
            self.regular_import_ns_prog.check_mypy_run(
                python_executable,
                [NamespaceMsg.cfm_beta,
                 NamespaceMsg.help_note,
                 NamespaceMsg.bool_str,
                 NamespaceMsg.int_bool],
                venv_dir=venv_dir,
            )


if __name__ == '__main__':
    main()