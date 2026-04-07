"""C11 code generator for C△ compiler."""

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
            # Handle specific imports: using sin from <math> -> math_sin()
            if hasattr(self, "_specific_imports") and callee in self._specific_imports:
                lib_name, generated_name = self._specific_imports[callee]
                # Use the actual generated function name from plib processing
                if generated_name:
                    callee = generated_name
                else:
                    # Fallback: construct from lib_name
                    prefix = self._current_alias.get(lib_name, lib_name)
                    callee = f"{prefix}_{callee}"
            # Handle namespace prefix like "lib:func" -> "lib:func" (leave as-is for plstd)
            # But "otherlib:func" -> "otherlib_func" for user libraries
            elif ":" in callee:
                if callee.startswith("lib:"):
                    pass  # lib: stays as-is for plstd
                else:
                    callee = callee.replace(":", "_")
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
        self._gen_imports()
        for decl in node.declarations:
            if not isinstance(decl, (UsingDecl, ExposeDecl, SpaceDecl)):
                # When structor is None, includes/defines were already handled in _gen_imports
                if self.structor is None and isinstance(decl, (Include, Define)):
                    continue
                self._gen_node(decl)

    def _gen_imports(self):
        """Generate import-related code (includes, exposes)."""
        if self.structor is None:
            # No structor - just emit includes/defines from AST directly
            for decl in self.ast.declarations:
                if isinstance(decl, Include):
                    self._gen_include(decl)
                elif isinstance(decl, Define):
                    self._emit(decl.directive)
            return

        imports = self.structor.get_imports()
        exposes = self.structor.get_exposes()

        local_imports = []
        alias_map = {}  # alias -> lib_name
        seen_libs = set()
        specific_imports = {}  # item -> lib_name (for "using X from <Y>")

        for imp in imports:
            source = imp.source
            if source.startswith("<") and source.endswith(">"):
                # Treat <math> as a plib lookup, not a system header
                lib_name = source[1:-1]
                if lib_name not in seen_libs:
                    local_imports.append(lib_name)
                    seen_libs.add(lib_name)
                if imp.alias:
                    alias_map[imp.alias] = (lib_name, "local")
                if imp.item:
                    # Track specific imports: using sin from <math>
                    specific_imports[imp.item] = lib_name
            elif "&" in source:
                pass  # No include needed for scoped imports (X&Y)
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
        self._specific_imports = {}
        self._current_alias = {}
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
            self._gen_plib_code(lib_name, alias)

        for exp in exposes:
            # Check if the library was imported first
            lib_imported = any(imp.source == exp.target for imp in imports)
            if not lib_imported:
                raise ValueError(
                    f"Cannot expose '{exp.target}' - it must be imported first. "
                    f'Use: using "{exp.target}" before exposing it.'
                )
            # Note: expose validation passes - the symbols are available via libname:func
            # Future: could add #defines to make symbols global without prefix

    def _gen_plib_code(self, lib_name: str, alias: str = None):
        """Generate code from a local plib file."""
        import os
        import lexer
        import parser as p

        search_dirs = []
        if self.source_path:
            search_dirs.append(os.path.dirname(self.source_path))
        search_dirs.extend(
            [
                ".",
                os.path.expanduser("~/.local/lib/PLIBS"),
                "/usr/lib/PLIBS",
            ]
        )

        plib_path = None
        for d in search_dirs:
            candidate = os.path.join(d, f"{lib_name}.plib")
            if os.path.exists(candidate):
                plib_path = candidate
                break

        if not plib_path:
            return

        with open(plib_path, "r") as f:
            plib_content = f.read()

        tokens = lexer.Lexer(plib_content).lex()
        tokens.append(("EOF", "EOF", 0, 0))
        plib_ast = p.Parser(tokens).parse_program()

        # Determine the prefix to use - alias if provided, else lib_name
        prefix = alias if alias else lib_name

        for decl in plib_ast.declarations:
            # Apply alias prefix to top-level declarations
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
                        # Update mapping: sin -> math_sin
                        if nested_decl.name in self._specific_imports:
                            _, old_namespace = self._specific_imports[nested_decl.name]
                            self._specific_imports[nested_decl.name] = (
                                lib_name,
                                generated_name,
                            )

                        nested_decl.name = generated_name
                    elif isinstance(nested_decl, p.Declaration):
                        nested_decl.name = f"{actual_prefix}_{nested_decl.name}"
                    self._gen_node(nested_decl)
            elif isinstance(
                decl, (p.Function, p.Declaration, p.StructDef, p.Typedef, p.EnumDef)
            ):
                self._gen_node(decl)

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
            self._gen_include(node)
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

        # Handle function prototypes: var_type is "void (func prototype)"
        if "(func prototype)" in node.var_type:
            # Extract return type and params from name if stored
            # For now, output a placeholder or skip
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

        if array_size is not None:
            if isinstance(array_size, list):
                dims = "".join(f"[{s}]" for s in array_size)
                name = f"{node.name}{dims}"
            else:
                name = f"{node.name}[{array_size}]"
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

    def _gen_include(self, node: Include):
        if node.is_system:
            self._emit(f"#include <{node.path}>")
        else:
            self._emit(f'#include "{node.path}"')

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
