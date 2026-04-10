"""C11 code generator for C△ compiler."""

import os

from parser import (
    Function,
    Declaration,
    Compound,
    If,
    While,
    Do,
    For,
    Return,
    Break,
    Continue,
    Goto,
    Label,
    ExprStmt,
    Binary,
    Unary,
    Literal,
    Var,
    Call,
    ArrayAccess,
    Cast,
    Assignment,
    Include,
    Define,
    InitList,
    Switch,
    StructDef,
    Typedef,
    UsingDecl,
    ExposeDecl,
    LibAccess,
    SpaceDecl,
    TypeExpr,
    FieldAccess,
    DesignatedInit,
    ArrayDesignation,
)


TYPE_MAP = {
    "string": "char*",
    "int": "int",
    "float": "float",
    "double": "double",
    "char": "char",
    "void": "void",
    "short": "short",
    "long": "long",
    "signed": "signed",
    "unsigned": "unsigned",
}


class CodeGen:
    def __init__(self, ast, structor, layouts=None, source_path=None):
        self.ast = ast
        self.structor = structor
        self.layouts = layouts or {}
        self.source_path = source_path  # path to source file for plib lookup
        self._lines = []
        self._indent = 0
        self._specific_imports = {}  # item -> (lib_name, namespace)
        self._current_alias = {}  # lib_name -> alias
        self._generating_plib = False  # Flag to bypass checks when generating plib code
        self._plstd_functions = set()  # Dynamically tracked plstd functions
        self._plstd_exposed = False  # Whether plstd has been exposed
        self._exposed_libs = set()  # Set of exposed library names
        self._collected_includes = set()

    def generate(self) -> str:
        """Main entry point. Returns generated C code as string."""
        self._lines = []
        self._gen_program(self.ast)
        return "\n".join(self._lines)

    # ------------------------------------------------------------------
    # Emission helpers
    # ------------------------------------------------------------------

    def _emit(self, line: str = ""):
        """Emit one complete source line at the current indent level."""
        indent = "    " * self._indent
        self._lines.append(indent + line)

    def _emit_raw(self, text: str):
        """Emit a pre-indented block of text verbatim (e.g. asm blocks)."""
        for line in text.splitlines():
            self._lines.append(line)

    # ------------------------------------------------------------------
    # Type mapping
    # ------------------------------------------------------------------

    def _map_type(self, typ: str) -> str:
        """Map C△ type to C type."""
        if not typ:
            return typ
        if typ.startswith("dynam "):
            typ = typ[7:]
        if typ.startswith("tuple "):
            typ = typ[6:]
        if typ == "string":
            return "char*"
        return TYPE_MAP.get(typ, typ)

    def _contains_assignment(self, node) -> bool:
        """Recursively check if an expression contains an assignment."""
        if node is None:
            return False
        if isinstance(node, Assignment):
            return True
        if isinstance(node, Binary):
            return self._contains_assignment(node.left) or self._contains_assignment(
                node.right
            )
        if isinstance(node, Unary):
            return self._contains_assignment(node.operand)
        if isinstance(node, Call):
            return any(self._contains_assignment(a) for a in node.args)
        return False

    # ------------------------------------------------------------------
    # Expression serialiser
    # ------------------------------------------------------------------

    def _expr(self, node) -> str:
        """Recursively serialise an expression node to a C string.

        This is the single source of truth for expression rendering.
        All statement generators call this rather than recursing through
        _gen_node, which avoids the interleaved-emit ordering bugs that
        arise when half the code uses end='' streaming and the other half
        uses full-line emission.
        """
        if node is None:
            return ""

        if isinstance(node, Literal):
            val = node.value
            if isinstance(val, str):
                # Already a string lexeme — pass through as-is if it looks
                # like a number or a quoted literal; otherwise wrap in quotes.
                stripped = val.lstrip("-")
                if (
                    stripped.startswith("0x")
                    or stripped.startswith("0X")
                    or stripped.startswith("0b")
                    or stripped.startswith("0B")
                    or stripped.isdigit()
                    or stripped.replace(".", "", 1).isdigit()
                    or val.startswith('"')
                    or val.startswith("'")
                    or any(
                        stripped.rstrip("uUlLfF").replace(".", "", 1).isdigit()
                        for _ in [None]
                    )
                ):
                    return val
                return f'"{val}"'
            return str(val)

        if isinstance(node, Var):
            return node.name

        if isinstance(node, TypeExpr):
            return self._map_type(node.type_name)

        if isinstance(node, Binary):
            if node.op == "?:":
                # Ternary: condition ?  true_val : false_val
                # Parser encodes as Binary("?:", cond, Binary("branch", t, f))
                cond = self._expr(node.left)
                if isinstance(node.right, Binary) and node.right.op == "branch":
                    true_val = self._expr(node.right.left)
                    false_val = self._expr(node.right.right)
                    return f"({cond}) ? {true_val} : {false_val}"
                return f"({cond}) ? {self._expr(node.right)}"
            left = self._expr(node.left)
            right = self._expr(node.right)
            # Comparison operators don't need extra parens (avoid gcc warnings)
            if node.op in ("==", "!=", "<", ">", "<=", ">="):
                return f"{left} {node.op} {right}"
            # Preserve AST grouping regardless of C operator precedence.
            return f"({left} {node.op} {right})"

        if isinstance(node, Unary):
            if node.op == "sizeof":
                operand = self._expr(node.operand)
                return f"sizeof({operand})"
            operand = self._expr(node.operand)
            if node.prefix:
                return f"{node.op}{operand}"
            return f"{operand}{node.op}"

        if isinstance(node, Assignment):
            target = self._expr(node.target)
            value = self._expr(node.value)
            return f"{target} = {value}"

        if isinstance(node, Call):
            callee = (
                node.callee.name
                if isinstance(node.callee, Var)
                else self._expr(node.callee)
            )

            # FIRST: Check if library is exposed
            # If exposed: printd@lib prefix is allowed but optional (strip it)
            # If NOT exposed: printd@lib prefix IS required for plstd functions
            # Skip this check when generating plib code itself
            # Check both the plstd flag and exposed_libs set for backwards compatibility
            plstd_exposed = (
                hasattr(self, "_plstd_exposed") and self._plstd_exposed
            ) or (hasattr(self, "_exposed_libs") and "plstd" in self._exposed_libs)
            if not self._generating_plib and plstd_exposed:
                # plstd is exposed - allow both direct call and printd@lib prefix
                actual_callee = callee
                if "@lib" in callee:
                    actual_callee = callee.split("@")[0]
                if actual_callee in self._plstd_functions:
                    callee = actual_callee
            elif not self._generating_plib and not plstd_exposed:
                # plstd not exposed - printd@lib prefix IS required for plstd functions
                actual_callee = callee
                if "@lib" in callee:
                    actual_callee = callee.split("@")[0]
                if actual_callee in self._plstd_functions:
                    if "@lib" not in callee:
                        raise ValueError(
                            f"Function '{actual_callee}' requires '{actual_callee}@lib()' syntax (plstd not exposed)."
                        )
                    callee = actual_callee

            # Handle specific imports: using sin from <math> -> math_sin()
            # AND intra-file scoped imports: using X&Y -> map Y to X_Y
            # This must run BEFORE namespace transformation so "printd@lib" can match "printd"
            base_callee = callee.split("@")[0] if "@" in callee else callee
            if (
                hasattr(self, "_specific_imports")
                and base_callee in self._specific_imports
            ):
                lib_name, func_name = self._specific_imports[base_callee]
                # Check if this is an intra-file scoped import (scope chain has &)
                # OR it's a simple scoped import (single scope name without &)
                # OR it's an alias (lib_name == func_name means alias was used)
                # Both need transformation: foo&bar -> foo_bar, main&helper -> main_helper
                # But alias: use directly
                if "&" in str(lib_name):
                    # Chain like a&b&c - transform
                    scope_chain = lib_name
                    callee = scope_chain.replace("&", "_") + "_" + func_name
                elif lib_name == func_name:
                    # Alias used - callee should already be the alias
                    pass  # callee stays as-is (already matches)
                else:
                    # Single scope like foo - transform to foo_bar
                    # OR plstd specific import: printd from <plstd> -> plstd_printd
                    if lib_name == "plstd":
                        # func_name is already the generated name like "plstd_printd"
                        callee = func_name
                    else:
                        callee = lib_name + "_" + func_name

            # Handle user namespace prefix like "helper@mylib" -> "mylib_helper"
            # Check AFTER specific imports since both may contain "@"
            elif "@" in callee and "@lib" not in callee:
                # Convert user namespace access to prefixed function name
                # Space generates: namespace_func, so call should be namespace_func
                parts = callee.split("@")
                if len(parts) == 2:
                    func, namespace = parts
                    callee = f"{namespace}_{func}"
            # Handle plstd namespace prefix like "printd@lib" -> "plstd_printd"
            # (only if not handled by specific imports above)
            elif "@lib" in callee:
                parts = callee.split("@")
                if len(parts) == 2:
                    func, namespace = parts
                    callee = f"{namespace}_{func}"
            args = ", ".join(self._expr(a) for a in node.args)
            return f"{callee}({args})"

        if isinstance(node, ArrayAccess):
            arr = self._expr(node.array)
            idx = self._expr(node.index)
            return f"{arr}[{idx}]"

        if isinstance(node, Cast):
            operand = self._expr(node.operand)
            cast_type = self._map_type(node.cast_type)
            return f"({cast_type})({operand})"

        if isinstance(node, FieldAccess):
            obj = self._expr(node.obj)
            # If object is a dereference (Unary *), use arrow notation
            # The Unary was added by the parser when converting -> to (*).field
            # So we don't need another *
            if isinstance(node.obj, Unary) and node.obj.op == "*":
                # Get the operand of the unary (the pointer variable)
                ptr = self._expr(node.obj.operand)
                return f"{ptr}->{node.field_name}"
            return f"{obj}.{node.field_name}"

        if isinstance(node, InitList):
            elems = ", ".join(self._expr(e) for e in node.elements)
            return f"{{{elems}}}"

        if isinstance(node, DesignatedInit):
            return f".{node.field} = {self._expr(node.value)}"

        if isinstance(node, ArrayDesignation):
            idx = self._expr(node.index)
            val = self._expr(node.value)
            if node.is_range and node.end_index:
                end = self._expr(node.end_index)
                return f"[{idx}...{end}] = {val}"
            return f"[{idx}] = {val}"

        if isinstance(node, CompoundLiteral):
            typ = self._map_type(node.lit_type)
            elems = ", ".join(self._expr(e) for e in node.elements)
            return f"({typ}){{{elems}}}"

        if isinstance(node, Generic):
            expr = self._expr(node.expr)
            assocs = ", ".join(f"{t}: {self._expr(v)}" for t, v in node.associations)
            return f"_Generic({expr}, {assocs})"

        # Fallback
        return f"/* unknown expr: {node.__class__.__name__} */"

    # ------------------------------------------------------------------
    # Top-level
    # ------------------------------------------------------------------

    def _gen_program(self, node):
        # First collect all includes from user file and all plib dependencies
        self._gen_imports()
        # Now emit collected includes at the very beginning (prepend)
        includes_to_emit = []
        for inc in sorted(self._collected_includes):
            includes_to_emit.append(inc)
        # Prepend includes before any existing code
        self._lines = includes_to_emit + self._lines
        # Now generate rest of code (plib functions included via _gen_plib_code)
        for decl in node.declarations:
            if isinstance(decl, SpaceDecl):
                # Handle SpaceDecl in main file - generate nested declarations with prefix
                prefix = decl.name
                for nested in decl.declarations:
                    if isinstance(nested, Function):
                        nested.name = f"{prefix}_{nested.name}"
                    elif isinstance(nested, Declaration):
                        nested.name = f"{prefix}_{nested.name}"
                    self._gen_node(nested)
            elif not isinstance(decl, (UsingDecl, ExposeDecl)):
                # Skip Include/Define - they were collected in _gen_imports via _collect_include
                if isinstance(decl, (Include, Define)):
                    continue
                self._gen_node(decl)

    def _emit_collected_includes(self):
        for inc in sorted(self._collected_includes):
            self._emit(inc)

    def _gen_imports(self):
        """Generate import-related code (includes, exposes)."""
        if self.structor is None:
            for decl in self.ast.declarations:
                if isinstance(decl, Include):
                    self._collect_include(decl)
                elif isinstance(decl, Define):
                    self._collected_includes.add(decl.directive)
            return

        # Collect user file includes from the AST
        for decl in self.ast.declarations:
            if isinstance(decl, Include):
                self._collect_include(decl)
            elif isinstance(decl, Define):
                self._collected_includes.add(decl.directive)

        imports = self.structor.get_imports()
        exposes = self.structor.get_exposes()

        local_imports = []
        alias_map = {}  # alias -> lib_name
        seen_libs = set()
        specific_imports = {}  # item -> lib_name (for "using X from <Y>")

        for imp in imports:
            source = imp.source
            if source.startswith("<") and source.endswith(">"):
                # Treat <lib> as a plib lookup, not a system header
                lib_name = source[1:-1]
                actual_path = f"plstd/{lib_name}"

                if lib_name not in seen_libs:
                    local_imports.append(actual_path)
                    seen_libs.add(lib_name)
                if imp.alias:
                    alias_map[imp.alias] = (actual_path, "local")
                if imp.item:
                    # Track specific imports: using sin from <math>
                    specific_imports[imp.item] = actual_path
            elif "&" in source:
                # Handle intra-file scoped imports: using X&Y or using a&b&c&Y
                # This imports a symbol Y from scope X (chain of scopes)
                # We need to track this and map the symbol accordingly
                parts = source.split("&")
                if len(parts) >= 2:
                    # Last part is the symbol being imported
                    # First part(s) are the scope chain
                    symbol_name = parts[-1]
                    scope_chain = "&".join(parts[:-1])
                    # If there's an alias, use it instead of scope chain
                    if imp.alias:
                        self._specific_imports[symbol_name] = (imp.alias, imp.alias)
                    else:
                        # Store for later mapping in _expr
                        self._specific_imports[symbol_name] = (scope_chain, symbol_name)
                pass
            else:
                if source not in seen_libs:
                    local_imports.append(source)
                    seen_libs.add(source)
                if imp.alias:
                    alias_map[imp.alias] = (source, "local")
                if imp.item:
                    specific_imports[imp.item] = source

        # Store for use in _expr
        # Map: bare_name -> (lib_name, namespace)
        # Preserve any scoped imports already added (from & in source)
        existing_scoped = dict(self._specific_imports)
        self._specific_imports = {}
        self._current_alias = {}
        # Add scoped imports back
        self._specific_imports.update(existing_scoped)
        # Add specific imports from "using X from Y"
        for item, lib_name in specific_imports.items():
            # Will be updated when plib is processed
            self._specific_imports[item] = (lib_name, None)

        for lib_name in local_imports:
            alias = None
            for a, (lib, type_) in alias_map.items():
                if lib == lib_name and type_ == "local":
                    alias = a
                    break
            if alias:
                self._current_alias[lib_name] = alias
            # Only collect includes from plib (don't generate code yet)
            self._collect_plib_includes(lib_name)

        # Now generate plib code after all includes are collected
        for lib_name in local_imports:
            alias = self._current_alias.get(lib_name)
            self._gen_plib_code(lib_name, alias)

        for exp in exposes:
            # Check if the library was imported first
            # Normalize: both <plstd> and "plstd" should match "plstd"
            exp_target = exp.target

            # Handle @ syntax: expose printd@plstd exposes a specific item from a library
            if "@" in exp_target:
                func_name, lib_name = exp_target.rsplit("@", 1)

                # Special case: "lib" is an alias for "plstd"
                # So expose printd@lib should work if <plstd> was imported
                check_libs = [lib_name]
                if lib_name == "lib":
                    check_libs.append("plstd")

                # Check if any of the potential libraries was imported
                # Also check for path-based imports like <plstd/printd>
                lib_imported = any(
                    (imp.source == l)
                    or (imp.source == f"<{l}>")
                    or (imp.source == f'"{l}"')
                    # Also check path-based imports: <plstd/printd> imports plstd
                    or (imp.source.startswith(f"<{l}/"))
                    for imp in imports
                    for l in check_libs
                )
                # Also check if specific function was imported (using printd from <plstd>)
                func_imported = any(imp.item == func_name for imp in imports)
                # Either library imported OR specific function imported is fine
                if not lib_imported and not func_imported:
                    raise ValueError(
                        f"Cannot expose '{exp.target}' - library must be imported first. "
                        f"Use: using <{lib_name}> or using {func_name} from <{lib_name}> before exposing it."
                    )
                # Track that this library is now exposed (using generic set)
                # Special case: "lib" is an alias for "plstd"
                if lib_name == "lib":
                    self._plstd_exposed = True
                self._exposed_libs.add(lib_name)
                self._exposed_libs.add(exp.target)
                continue

            if exp_target.startswith("<") and exp_target.endswith(">"):
                exp_target = exp.target[1:-1]

            # Check if the library was imported
            # OR if this specific item was imported (e.g., using printd from <plstd>)
            lib_imported = any(
                (imp.source == exp_target)
                or (imp.source == f"<{exp_target}>")
                or (imp.source == f'"{exp_target}"')
                # Check if the specific item was imported
                or (imp.item == exp_target)
                for imp in imports
            )
            if not lib_imported:
                # Also check path-based imports
                if any(
                    imp.source.startswith(f"<{exp_target}/")
                    or imp.source == f"<{exp_target}>"
                    or imp.source == exp_target
                    for imp in imports
                ):
                    lib_imported = True
            # Track that this library is now exposed
            if exp.target == "plstd" or exp_target == "plstd":
                self._plstd_exposed = True
            elif exp.target.startswith("<") and exp.target.endswith(">"):
                lib_name = exp.target[1:-1]
                self._exposed_libs.add(lib_name)
            else:
                self._exposed_libs.add(exp.target)

    def _gen_plib_code(self, lib_name: str, alias: str = None):
        """Generate code from a local plib file."""
        import os
        import lexer
        import parser as p

        # Bypass the plstd check when generating plib code itself
        old_generating = self._generating_plib
        self._generating_plib = True

        plib_path = None
        search_name = lib_name.split("/")[-1]

        # Always check current directory first
        current_dir = os.path.dirname(self.source_path) if self.source_path else "."
        search_paths = [current_dir] + self._get_plibs_search_dirs()

        # Handle path with folder: plstd/printd -> import first .plib in folder
        if "/" in lib_name:
            folder = lib_name.split("/")[0]
            for base in search_paths:
                folder_path = os.path.join(base, folder)
                if os.path.isdir(folder_path):
                    for f in sorted(os.listdir(folder_path)):
                        if f.endswith(".plib"):
                            plib_path = os.path.join(folder_path, f)
                            break
                    if plib_path:
                        break
        else:
            # First try direct .plib file
            for base in search_paths:
                candidate = os.path.join(base, f"{search_name}.plib")
                if os.path.exists(candidate):
                    plib_path = candidate
                    break

            # If no .plib file found, try as folder (import all .plib files in folder)
            if not plib_path:
                for base in search_paths:
                    folder_path = os.path.join(base, search_name)
                    if os.path.isdir(folder_path):
                        # Import first .plib in folder
                        for f in sorted(os.listdir(folder_path)):
                            if f.endswith(".plib"):
                                plib_path = os.path.join(folder_path, f)
                                break
                        if plib_path:
                            break

        if not plib_path:
            return

        with open(plib_path, "r") as f:
            plib_content = f.read()

        tokens = lexer.Lexer(plib_content).lex()
        tokens.append(("EOF", "EOF", 0, 0))
        plib_ast = p.Parser(tokens).parse_program()

        # Collect all includes from the plib (not emit - collected for later)
        for decl in plib_ast.declarations:
            if isinstance(decl, p.Include):
                self._collect_include(decl)
            elif isinstance(decl, p.Define):
                # Collect defines into a set too for later deduplication
                self._collected_includes.add(decl.directive)

        # Determine the prefix to use - alias if provided, else lib_name
        # For plstd/plstd, use "plstd" as prefix to avoid name conflicts
        if alias:
            prefix = alias
        elif lib_name.startswith("plstd/"):
            prefix = "plstd"  # Use "plstd" prefix for plstd/plstd
            self._plstd_exposed = True  # Auto-expose when importing via folder
        elif "/" in lib_name:
            # Any other folder import - extract folder name as prefix
            prefix = lib_name.split("/")[0]
            self._exposed_libs.add(prefix)
        else:
            prefix = lib_name

        for decl in plib_ast.declarations:
            # Skip includes/defines - already handled above
            if isinstance(decl, (p.Include, p.Define)):
                continue

            # Apply prefix to top-level declarations only if alias is provided
            # For plstd imports without alias (using printd from <plstd>), don't prefix
            if alias:
                if isinstance(decl, p.Function):
                    decl.name = f"{prefix}_{decl.name}"
                elif isinstance(decl, p.Declaration):
                    decl.name = f"{prefix}_{decl.name}"

            # Handle SpaceDecl - generate with prefix + namespace prefix
            if isinstance(decl, p.SpaceDecl):
                # Determine actual generated prefix
                if prefix == decl.name:
                    actual_prefix = prefix  # e.g., math_sin
                else:
                    actual_prefix = f"{prefix}_{decl.name}"  # e.g., lib_utils_func

                # Update specific imports mapping with actual generated prefix
                for nested_decl in decl.declarations:
                    if isinstance(nested_decl, p.Function):
                        generated_name = f"{actual_prefix}_{nested_decl.name}"
                        # Update mapping: printd -> (plstd, plstd_printd)
                        if nested_decl.name in self._specific_imports:
                            self._specific_imports[nested_decl.name] = (
                                lib_name.split("/")[-1],
                                generated_name,
                            )

                        nested_decl.name = generated_name
                    elif isinstance(nested_decl, p.Declaration):
                        nested_decl.name = f"{actual_prefix}_{nested_decl.name}"
                    self._gen_node(nested_decl)
            elif isinstance(
                decl, (p.Function, p.Declaration, p.StructDef, p.Typedef, p.EnumDef)
            ):
                # Update specific_imports mapping for this function
                if isinstance(decl, p.Function) and decl.name in self._specific_imports:
                    # The decl.name already has the prefix applied (see line ~558)
                    # Just use it directly
                    self._specific_imports[decl.name] = (
                        lib_name.split("/")[-1],
                        decl.name,
                    )

                # Track plstd functions if we're generating plstd
                if lib_name.startswith("plstd/") and isinstance(decl, p.Function):
                    self._plstd_functions.add(decl.name)

                self._gen_node(decl)

        # Restore the flag after generating plib code
        self._generating_plib = old_generating

    def _get_plibs_search_dirs(self):
        """Return list of directories to search for plib files."""
        return [
            os.path.expanduser("~/.local/lib/PLIBS"),
            "/usr/lib/PLIBS",
        ]

    def _collect_plib_includes(self, lib_name: str, alias: str = None):
        """Collect includes from a plib file into self._collected_includes."""
        import os
        import lexer
        import parser as p

        search_dirs = []
        if self.source_path:
            search_dirs.append(os.path.dirname(self.source_path))

        if "/" in lib_name:
            folder, filename = lib_name.split("/", 1)
            search_dirs.extend(
                [
                    os.path.expanduser(f"~/.local/lib/PLIBS/{folder}"),
                    f"/usr/lib/PLIBS/{folder}",
                ]
            )
        else:
            search_dirs.extend(
                [
                    ".",
                    os.path.expanduser("~/.local/lib/PLIBS"),
                    "/usr/lib/PLIBS",
                ]
            )

        plib_path = None
        search_name = lib_name.split("/")[-1]

        # For paths like "folder/name" (e.g., "plstd/plstd"), look in folder subdirectory
        if "/" in lib_name:
            folder = lib_name.split("/")[0]
            for base in ["."] + self._get_plibs_search_dirs():
                folder_path = os.path.join(base, folder)
                if os.path.isdir(folder_path):
                    # Find any .plib file in the folder
                    for f in os.listdir(folder_path):
                        if f.endswith(".plib"):
                            plib_path = os.path.join(folder_path, f)
                            break
                    if plib_path:
                        break

        if not plib_path:
            # Standard search
            for d in self._get_plibs_search_dirs():
                candidate = os.path.join(d, f"{search_name}.plib")
                if os.path.exists(candidate):
                    plib_path = candidate
                    break

        if not plib_path:
            return

        self._do_collect_plib_includes(plib_path)

    def _do_collect_plib_includes(self, plib_path: str):
        """Actually parse plib and collect its includes."""
        import lexer
        import parser as p

        with open(plib_path, "r") as f:
            plib_content = f.read()

        tokens = lexer.Lexer(plib_content).lex()
        tokens.append(("EOF", "EOF", 0, 0))
        plib_ast = p.Parser(tokens).parse_program()

        for decl in plib_ast.declarations:
            if isinstance(decl, p.Include):
                self._collect_include(decl)

    # ------------------------------------------------------------------
    # Statement / declaration dispatcher
    # ------------------------------------------------------------------

    def _gen_node(self, node):
        """Dispatch to the appropriate generator."""
        if isinstance(node, Function):
            self._gen_function(node)
        elif isinstance(node, Declaration):
            self._gen_declaration(node)
        elif isinstance(node, Compound):
            self._gen_compound_block(node)
        elif isinstance(node, If):
            self._gen_if(node)
        elif isinstance(node, While):
            self._gen_while(node)
        elif isinstance(node, Do):
            self._gen_do(node)
        elif isinstance(node, For):
            self._gen_for(node)
        elif isinstance(node, Return):
            self._gen_return(node)
        elif isinstance(node, Break):
            self._emit("break;")
        elif isinstance(node, Continue):
            self._emit("continue;")
        elif isinstance(node, Goto):
            self._emit(f"goto {node.label};")
        elif isinstance(node, Label):
            # Labels are dedented by one level in C convention
            indent = "    " * max(0, self._indent - 1)
            self._lines.append(f"{indent}{node.name}:")
        elif isinstance(node, ExprStmt):
            self._gen_expr_stmt(node)
        elif isinstance(node, Include):
            self._collect_include(node)
        elif isinstance(node, Define):
            # Emit #define directives as-is
            self._emit(node.directive)
        elif isinstance(node, Switch):
            self._gen_switch(node)
        elif isinstance(node, StructDef):
            self._gen_struct_def(node)
        elif isinstance(node, Typedef):
            self._gen_typedef(node)
        elif hasattr(node, "node_type") and node.node_type == "ASM":
            self._gen_asm_block(node)
        elif hasattr(node, "__class__") and node.__class__.__name__ == "AsmBlock":
            self._gen_asm_block(node)
        elif isinstance(node, UsingDecl):
            pass  # Imports handled in header generation
        elif isinstance(node, ExposeDecl):
            pass  # Expose handled in header generation
        elif isinstance(node, LibAccess):
            pass  # Handled in expression context
        elif isinstance(node, SpaceDecl):
            for decl in node.declarations:
                self._gen_statement(decl)
        elif node is None:
            pass  # Skip None declarations (e.g., skipped extern "C" blocks)
        else:
            # Expression used as a statement (e.g. bare assignment at top level)
            self._emit(f"{self._expr(node)};")

    # ------------------------------------------------------------------
    # Statement generators
    # ------------------------------------------------------------------

    def _gen_function(self, node: Function):
        ret_type = self._map_type(node.ret_type)
        params = []
        for p in node.params:
            ptype = p[0]
            pname = p[1]
            psize = p[2] if len(p) > 2 else None
            if ptype == "...":
                params.append("...")
            else:
                param_str = f"{self._map_type(ptype)} {pname}"
                if psize is not None:
                    if isinstance(psize, list):
                        param_str += "[]"
                        for s in psize[1:]:
                            if s == 0:
                                param_str += "[]"
                            else:
                                param_str += f"[{s}]"
                    else:
                        if psize == 0:
                            param_str += "[]"
                        else:
                            param_str += f"[{psize}]"
                params.append(param_str)
        param_str = ", ".join(params) if params else "void"
        self._emit(f"{ret_type} {node.name}({param_str}) {{")
        self._indent += 1
        if isinstance(node.body, Compound):
            for stmt in node.body.stmts:
                self._gen_node(stmt)
        else:
            self._gen_node(node.body)
        self._indent -= 1
        self._emit("}")
        self._emit("")  # blank line after function

    def _gen_declaration(self, node: Declaration):
        typ = self._map_type(node.var_type)
        name = node.name
        array_size = getattr(node, "array_size", None)

        # Handle string to char array conversion: char s[] = "hello"
        if typ == "char" and node.initializer is not None:
            init_val = node.initializer
            if (
                hasattr(init_val, "value")
                and isinstance(init_val.value, str)
                and init_val.value.startswith('"')
            ):
                # Convert string to char array: "hello" -> {'h','e','l','l','o','\0'}
                s = init_val.value[1:-1]  # Remove quotes
                chars = [f"'{c}'" if c not in '\\"' else f"'{c}'" for c in s]
                chars.append("'\\0'")  # Add null terminator
                init_str = "{" + ", ".join(chars) + "}"
                if array_size is None:
                    # Infer size from string length + null
                    array_size = len(chars)
                    name = f"{name}[{array_size}]"
                else:
                    name = f"{name}[{array_size}]"
                self._emit(f"char {name} = {init_str};")
                return

        # Handle function prototypes: var_type is "void (func prototype)"
        if "(func prototype)" in node.var_type:
            # Extract return type and params from name if stored
            actual_type = node.var_type.replace(" (func prototype)", "")
            actual_type = self._map_type(actual_type)
            # Try to get params from node if available
            params = getattr(node, "params", None)
            if params:
                param_str = ", ".join(f"{self._map_type(p[0])} {p[1]}" for p in params)
                self._emit(f"{actual_type} {name}({param_str});")
            else:
                self._emit(f"{actual_type} {name}(void);")
            return

        # Collect dimensions from both array_size and dimensions
        dims = getattr(node, "dimensions", None)

        # Build full dimension list: use array_size for 1D, dimensions for multi-dim
        dim_list = []

        # Build full dimension list from array_size and/or dimensions
        if dims and isinstance(dims, list):
            # Use dimensions list directly (contains all dims)
            dim_list = list(dims)
        elif array_size is not None:
            # Single dimension from array_size
            if isinstance(array_size, list):
                dim_list = list(array_size)
            else:
                dim_list = [array_size]

        # If we have None in any position, try to infer from initializer
        init = node.initializer

        def count_elements_at_depth(init_list, depth):
            """Count elements at given nesting depth."""
            if depth < 0 or not init_list or not hasattr(init_list, "elements"):
                return 0
            if depth == 0:
                return len(init_list.elements) if init_list.elements else 0
            # Go deeper: use first element at each level
            if init_list.elements and init_list.elements[0]:
                return count_elements_at_depth(init_list.elements[0], depth - 1)
            return 0

        # Process each dimension position
        for i, d in enumerate(dim_list):
            if d is None:
                if init:
                    # Try to infer from initializer at this depth
                    inferred = count_elements_at_depth(init, i)
                    if inferred > 0:
                        dim_list[i] = inferred
                    else:
                        raise ValueError(
                            f"Cannot infer dimension {i + 1} for array '{node.name}' - "
                            f"provide explicit size or ensure initializer has values at this level"
                        )
                else:
                    raise ValueError(
                        f"Cannot infer dimension {i + 1} for array '{node.name}' - "
                        f"provide explicit size or initializer"
                    )

        # Build final name with all dimensions
        def is_valid_dim(d):
            if d is None:
                return False
            if isinstance(d, list):
                return any(is_valid_dim(x) for x in d)
            return isinstance(d, int) and d > 0

        dim_str = ""
        for d in dim_list:
            if d is None:
                continue
            if isinstance(d, list):
                dim_str += "".join(f"[{x}]" for x in d if x and isinstance(x, int))
            elif isinstance(d, int) and d > 0:
                dim_str += f"[{d}]"
        if dim_str:
            name = f"{node.name}{dim_str}"

        # Emit the declaration
        if node.initializer is not None:
            val = self._expr(node.initializer)
            self._emit(f"{typ} {name} = {val};")
        else:
            self._emit(f"{typ} {name};")

    def _gen_compound_block(self, node: Compound):
        """Emit a braced block.  Used when a Compound appears as a standalone
        statement rather than as a function body (which is handled inline)."""
        if getattr(node, "_is_decl_list", False):
            for stmt in node.stmts:
                self._gen_node(stmt)
            return
        self._emit("{")
        self._indent += 1
        for stmt in node.stmts:
            self._gen_node(stmt)
        self._indent -= 1
        self._emit("}")

    def _gen_if(self, node: If):
        cond = self._expr(node.cond)
        # Don't add extra parens if condition is already a comparison (causes warnings)
        if isinstance(node.cond, Binary) and node.cond.op in (
            "==",
            "!=",
            "<",
            ">",
            "<=",
            ">=",
        ):
            self._emit(f"if ({cond}) {{")
        else:
            self._emit(f"if ({cond}) {{")
        self._indent += 1
        body = node.then_branch
        if isinstance(body, Compound):
            for stmt in body.stmts:
                self._gen_node(stmt)
        else:
            self._gen_node(body)
        self._indent -= 1
        if node.else_branch is not None:
            self._emit("} else {")
            self._indent += 1
            if isinstance(node.else_branch, Compound):
                for stmt in node.else_branch.stmts:
                    self._gen_node(stmt)
            else:
                self._gen_node(node.else_branch)
            self._indent -= 1
            self._emit("}")
        else:
            self._emit("}")

    def _gen_while(self, node: While):
        cond = self._expr(node.cond)
        # Detect the problematic pattern: (assignment != comparison)
        # This happens when parsing "entry = readdir(dir) != NULL"
        # which becomes Binary("!=", Assignment(...), NULL)
        # Output: while ((entry = readdir(dir)) != NULL) {
        # NOT: while ((entry = readdir(dir) != NULL)) {

        # Check if it's a binary with assignment on the left and comparison
        needs_fix = False
        if isinstance(node.cond, Binary) and isinstance(node.cond.left, Assignment):
            # The binary left is an assignment - need special handling
            needs_fix = True

        if needs_fix:
            # Reconstruct: (assignment) op right
            left = self._expr(node.cond.left)
            op = node.cond.op
            right = self._expr(node.cond.right)
            cond = f"({left}) {op} {right}"

        self._emit(f"while ({cond}) {{")
        self._indent += 1
        body = node.body
        if isinstance(body, Compound):
            for stmt in body.stmts:
                self._gen_node(stmt)
        else:
            self._gen_node(body)
        self._indent -= 1
        self._emit("}")

    def _gen_do(self, node: Do):
        cond = self._expr(node.cond)
        self._emit("do {")
        self._indent += 1
        body = node.body
        if isinstance(body, Compound):
            for stmt in body.stmts:
                self._gen_node(stmt)
        else:
            self._gen_node(body)
        self._indent -= 1
        self._emit(f"}} while ({cond});")

    def _gen_for(self, node: For):
        init_parts = []
        first_type = None
        if node.init is not None:
            if isinstance(node.init, Compound):
                for stmt in node.init.stmts:
                    if isinstance(stmt, Declaration):
                        if first_type is None:
                            first_type = self._map_type(stmt.var_type)
                            typ = first_type
                        else:
                            typ = ""
                        name = stmt.name
                        if stmt.initializer is not None:
                            if typ:
                                init_parts.append(
                                    f"{typ} {name} = {self._expr(stmt.initializer)}"
                                )
                            else:
                                init_parts.append(
                                    f"{name} = {self._expr(stmt.initializer)}"
                                )
                        else:
                            if typ:
                                init_parts.append(f"{typ} {name}")
                            else:
                                init_parts.append(name)
                    else:
                        init_parts.append(self._expr(stmt))
            elif isinstance(node.init, Declaration):
                first_type = self._map_type(node.init.var_type)
                typ = first_type
                name = node.init.name
                if node.init.initializer is not None:
                    init_parts.append(
                        f"{typ} {name} = {self._expr(node.init.initializer)}"
                    )
                else:
                    init_parts.append(f"{typ} {name}")
            else:
                init_parts.append(self._expr(node.init))
        init_str = ", ".join(init_parts)

        cond_str = self._expr(node.cond) if node.cond else ""

        post_parts = []
        if node.post is not None:
            if isinstance(node.post, Compound):
                for stmt in node.post.stmts:
                    post_parts.append(self._expr(stmt))
            else:
                post_parts.append(self._expr(node.post))
        post_str = ", ".join(post_parts)

        self._emit(f"for ({init_str}; {cond_str}; {post_str}) {{")
        self._indent += 1
        body = node.body
        if isinstance(body, Compound):
            for stmt in body.stmts:
                self._gen_node(stmt)
        else:
            self._gen_node(body)
        self._indent -= 1
        self._emit("}")

    def _gen_return(self, node: Return):
        if node.expr is not None:
            self._emit(f"return {self._expr(node.expr)};")
        else:
            self._emit("return;")

    def _gen_expr_stmt(self, node: ExprStmt):
        if node.expr is not None:
            self._emit(f"{self._expr(node.expr)};")

    def _collect_include(self, node: Include):
        if node.is_system:
            self._collected_includes.add(f"#include <{node.path}>")
        else:
            self._collected_includes.add(f'#include "{node.path}"')

    def _gen_switch(self, node: Switch):
        expr = self._expr(node.expr)
        self._emit(f"switch ({expr}) {{")
        self._indent += 1
        for case_val, body in node.cases:
            if case_val is None:
                self._emit("default:")
            else:
                self._emit(f"case {self._expr(case_val)}:")
            self._indent += 1
            if isinstance(body, Compound):
                for stmt in body.stmts:
                    self._gen_node(stmt)
            else:
                self._gen_node(body)
            self._indent -= 1
        self._indent -= 1
        self._emit("}")

    def _gen_struct_def(self, node):
        name = getattr(node, "name", "") or ""
        fields = getattr(node, "fields", []) or []
        header = f"struct {name}" if name else "struct"
        self._emit(f"{header} {{")
        self._indent += 1
        for field_type, field_name in fields:
            self._emit(f"{field_type} {field_name};")
        self._indent -= 1
        self._emit("};")  # struct definition always ends with ;

    def _gen_typedef(self, node):
        actual = getattr(node, "actual_type", "")
        alias = getattr(node, "alias", "")
        self._emit(f"typedef {actual} {alias};")

    def _gen_asm_block(self, node):
        """Pass asm blocks through verbatim."""
        content = getattr(node, "content", "") or str(node)
        self._emit_raw(content)
