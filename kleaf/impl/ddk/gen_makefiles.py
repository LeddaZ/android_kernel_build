# Copyright (C) 2022 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Generate a DDK module Makefile
"""

import absl.flags.argparse_flags
import argparse
import json
import logging
import os
import pathlib
import shlex
import sys
import textwrap
from typing import Optional, TextIO

_SOURCE_SUFFIXES = (
    ".c",
    ".rs",
    ".s",
)


def die(*args, **kwargs):
    logging.error(*args, **kwargs)
    sys.exit(1)


def _gen_makefile(
        package: pathlib.Path,
        module_symvers_list: list[pathlib.Path],
        output_makefile: pathlib.Path,
):
    # kernel_module always executes in a sandbox. So ../ only traverses within
    # the sandbox.
    rel_root = os.path.join(*([".."] * len(package.parts)))

    content = ""

    for module_symvers in module_symvers_list:
        content += textwrap.dedent(f"""\
            # Include symbol: {module_symvers}
            EXTRA_SYMBOLS += $(OUT_DIR)/$(M)/{rel_root}/{module_symvers}
            """)

    content += textwrap.dedent("""\
        modules modules_install clean:
        \t$(MAKE) -C $(KERNEL_SRC) M=$(M) $(KBUILD_OPTIONS) KBUILD_EXTRA_SYMBOLS="$(EXTRA_SYMBOLS)" $(@)
        """)

    os.makedirs(output_makefile.parent, exist_ok=True)
    with open(output_makefile, "w") as out_file:
        out_file.write(content)


def _write_ccflag(out_file, ccflag):
    out_file.write(textwrap.dedent(f"""\
        ccflags-y += {shlex.quote(ccflag)}
        """))


def gen_ddk_makefile(
        output_makefiles: pathlib.Path,
        kernel_module_out: pathlib.Path,
        kernel_module_srcs: list[pathlib.Path],
        include_dirs: list[pathlib.Path],
        module_symvers_list: list[pathlib.Path],
        package: pathlib.Path,
        local_defines: list[str],
        copt_file: Optional[TextIO],
):
    _gen_makefile(
        package=package,
        module_symvers_list=module_symvers_list,
        output_makefile=output_makefiles / "Makefile",
    )

    rel_srcs = []
    for src in kernel_module_srcs:
        if src.is_relative_to(package):
            rel_srcs.append(src.relative_to(package))

    if kernel_module_out.suffix != ".ko":
        die("Invalid output: %s; must end with .ko", kernel_module_out)

    kbuild = output_makefiles / kernel_module_out.parent / "Kbuild"
    os.makedirs(kbuild.parent, exist_ok=True)

    with open(kbuild, "w") as out_file:
        out_file.write(textwrap.dedent(f"""\
            # Build {package / kernel_module_out}
            obj-m += {kernel_module_out.with_suffix('.o').name}
            """))
        out_file.write("\n")

        for src in rel_srcs:
            # Ignore non-exported headers specified in srcs
            if src.suffix.lower() in (".h"):
                continue
            if src.suffix.lower() not in _SOURCE_SUFFIXES:
                die("Invalid source %s", src)
            # Ignore self (don't omit obj-foo += foo.o)
            if src.with_suffix(".ko") == kernel_module_out:
                out_file.write(textwrap.dedent(f"""\
                    # The module {kernel_module_out} has a source file {src}
                """))
                continue
            if not src.is_relative_to(kernel_module_out.parent):
                die("%s is not a valid source because it is not under %s",
                    src, kernel_module_out.parent)
            out = src.with_suffix(".o").relative_to(kernel_module_out.parent)
            out_file.write(textwrap.dedent(f"""\
                # Source: {package / src}
                {kernel_module_out.with_suffix('').name}-y += {out}
            """))

        out_file.write("\n")

        #    //path/to/package:target/name/foo.ko
        # =>   path/to/package/target/name
        rel_root_reversed = pathlib.Path(package) / kernel_module_out.parent
        rel_root = pathlib.Path(*([".."] * len(rel_root_reversed.parts)))

        for include_dir in include_dirs:
            out_file.write(textwrap.dedent(f"""\
                # Include {include_dir}
                """))
            _write_ccflag(out_file, f"-I$(srctree)/$(src)/{rel_root}/{include_dir}")

        if local_defines:
            out_file.write("\n")
            out_file.write(textwrap.dedent("""\
                # local defines
                """))

        for local_define in local_defines:
            _write_ccflag(out_file, f"-D{local_define}")

        _handle_copt_file(out_file, copt_file, rel_root)

    top_kbuild = output_makefiles / "Kbuild"
    if top_kbuild != kbuild:
        os.makedirs(output_makefiles, exist_ok=True)
        with open(top_kbuild, "w") as out_file:
            out_file.write(textwrap.dedent(f"""\
                # Build {package / kernel_module_out}
                obj-y += {kernel_module_out.parent}/
                """))

def _handle_copt_file(out_file: TextIO, copt_file: Optional[TextIO], rel_root: pathlib.Path):
    if not copt_file:
        return

    out_file.write("\n")
    out_file.write(textwrap.dedent("""\
        # copts
        """))

    for d in json.load(copt_file):
        expanded: str = d["expanded"]
        is_path: bool = d["is_path"]

        if is_path:
            expanded = str(rel_root / expanded)

        _write_ccflag(out_file, expanded)

if __name__ == "__main__":
    # argparse_flags.ArgumentParser only accepts --flagfile if there
    # are some DEFINE'd flags
    # https://github.com/abseil/abseil-py/issues/199
    absl.flags.DEFINE_string("flagfile_hack_do_not_use", "", "")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = absl.flags.argparse_flags.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=pathlib.Path)
    parser.add_argument("--kernel-module-out", type=pathlib.Path)
    parser.add_argument("--kernel-module-srcs", type=pathlib.Path, nargs="*", default=[])
    parser.add_argument("--output-makefiles", type=pathlib.Path)
    parser.add_argument("--include-dirs", type=pathlib.Path, nargs="*", default=[])
    parser.add_argument("--module-symvers-list", type=pathlib.Path, nargs="*", default=[])
    parser.add_argument("--local-defines", nargs="*", default=[])

    parser.add_argument("--copt-file", type=argparse.FileType("r"))

    gen_ddk_makefile(**vars(parser.parse_args()))