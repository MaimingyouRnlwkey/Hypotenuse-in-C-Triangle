class Scope:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.children = {}  # generic children (non-Callee/Caller)
        self.callees = {}  # name -> Callee objects
        self.callers = {}  # name -> Caller objects

    def __repr__(self):
        """Readable representation showing the scope name and its parent."""
        parent_name = self.parent.name if self.parent else None
        return f"Scope(name={self.name!r}, parent={parent_name!r})"

    def add_child(self, node):
        """Register a node in the appropriate collection.

        * Callee -> ``self.callees``
        * Caller -> ``self.callers``
        * Anything else -> ``self.children``
        """
        if isinstance(node, Callee):
            target = self.callees
        elif isinstance(node, Caller):
            target = self.callers
        else:
            target = self.children

        if node.name in target:
            raise ValueError(
                f"Child named `{node.name}` already exists in scope `{self.name}`"
            )
        target[node.name] = node
        return node

    def called(self, name):
        if name in self.children:
            return self.children[name]
        if name in self.callees:
            return self.callees[name]
        if name in self.callers:
            return self.callers[name]
        if self.parent:
            return self.parent.called(name)
        return None


class Node:
    """Base node for values and dependencies."""

    def __init__(self, name, scope):
        self.name = name
        self.scope = scope
        self.scope.add_child(self)
        self.dependencies = []

    def eval(self):
        raise NotImplementedError


class Callee(Node):
    """Node that provides a value or a function."""

    def __init__(self, name, scope, value):
        super().__init__(name, scope)
        self.value = value

    def __repr__(self):
        val_repr = (
            repr(self.value)
            if not isinstance(self.value, Node)
            else f"<Node {type(self.value).__name__}>"
        )
        return (
            f"Callee(name={self.name!r}, value={val_repr}, scope={self.scope.name!r})"
        )

    def eval(self, *args, **kwargs):
        if callable(self.value):
            resolved_args = [
                arg.eval() if isinstance(arg, Node) else arg for arg in args
            ]
            return self.value(*resolved_args)
        if isinstance(self.value, Node):
            return self.value.eval()
        return self.value


class Caller(Node):
    """Node that can depend on other nodes and call function nodes."""

    def __init__(self, name, scope, value=None):
        super().__init__(name, scope)
        self.value = value
        self.callee_children: dict | None = None

    def __repr__(self):
        if not self.dependencies:
            return f"Caller(name={self.name!r}, scope={self.scope.name!r}, args=[])"
        callee_node, args = self.dependencies[0]
        args_repr = []
        for arg_tokens in args:
            token_strs = ", ".join(f"{t[0]}:{t[1]!r}" for t in arg_tokens)
            args_repr.append(f"[{token_strs}]")
        return (
            f"Caller(name={self.name!r}, scope={self.scope.name!r}, "
            f"callee={callee_node.name!r}, args={args_repr})"
        )

    def call(self, node, *args):
        """Depend on a node. Arguments can be nodes or literals."""
        self.dependencies.append((node, args))

    def eval(self):
        if isinstance(self.value, Node):
            result = self.value.eval()
        else:
            result = self.value if isinstance(self.value, (int, float)) else 0

        for node, args in self.dependencies:
            if node is None:
                raise ValueError(f"callee '{node.name}' not found")
            result += node.eval(*args)
        return result


class Lib:
    """Library scope containing callable or value nodes."""

    def __init__(self, name, parent_scope=None):
        self.name = name
        self.scope = Scope(name, parent_scope)
        if parent_scope:
            parent_scope.add_child(self.scope)

    def add_node(self, node):
        if node.name in self.scope.children:
            return self.scope.children[node.name]
        return self.scope.add_child(node)

    def called(self, name):
        return self.scope.called(name)


class Token:
    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    def __repr__(self):
        return f"Token({self.type!r}, {self.value!r})"


class Structor:
    """Automatically structures each line of code.

    The parser implementation is injected via the ``parser`` argument to the
    constructor, removing the need for a hard-coded import.
    """

    def __init__(self, tokens_array, parser):
        self.tokens = tokens_array
        self.pos = 0
        self.objects = {}
        self.parser = parser

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def match(self, *types):
        tok = self.peek()
        if tok is None:
            return None
        tok_type = tok[0] if isinstance(tok, tuple) else getattr(tok, "type", None)
        if tok_type in types:
            return self.advance()
        return None

    def collect_args(self):
        """Collect function-call arguments as lists of raw tokens."""
        raw_args = []
        current = []
        while True:
            tok_peek = self.peek()
            if tok_peek is None:
                break
            t_type = (
                tok_peek[0]
                if isinstance(tok_peek, tuple)
                else getattr(tok_peek, "type", None)
            )
            if t_type == "RPAREN":
                break
            tok = self.advance()
            if isinstance(tok, tuple):
                t_type = tok[0]
            else:
                t_type = getattr(tok, "type", None)
            if t_type == "COMMA":
                raw_args.append(current)
                current = []
                continue
            current.append(tok)
        if current:
            raw_args.append(current)
        # Fix: peek() returns a (type, value) tuple, not a bare string.
        # Use _type() style check so the closing ')' is actually consumed.
        nxt = self.peek()
        if nxt is not None and (
            nxt[0] if isinstance(nxt, tuple) else getattr(nxt, "type", None)
        ) == "RPAREN":
            self.advance()
        return raw_args

    # ------------------------------------------------------------------
    # Helper: parse a numeric literal value from a token list.
    # Handles optional leading MINUS for negative numbers.
    # Fix for issue #61: correctly resolves '-500' instead of '-'.
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_literal_value(value_tokens, _type_fn, _value_fn):
        """Return a Python int/float/str from a list of value tokens.

        Recognises an optional leading MINUS token so that '-500' is stored
        as the integer -500 rather than the string '-'.

        If MINUS is present but is NOT followed by a numeric literal (e.g.
        it precedes an identifier like '-x'), the MINUS value is returned
        and the following token is left in the iterator for the caller to
        process.
        """
        if not value_tokens:
            return None

        is_negative = False
        token_iter = iter(value_tokens)
        first = next(token_iter, None)

        if _type_fn(first) == "MINUS":
            second = next(token_iter, None)
            if second is not None and _type_fn(second) in ("INT_LITERAL", "FLOAT_LITERAL"):
                is_negative = True
                first = second
            else:
                return _value_fn(first)

        val_type = _type_fn(first)
        val_content = _value_fn(first)

        if val_type == "INT_LITERAL" and val_content is not None:
            try:
                result = int(val_content)
            except ValueError:
                result = float(val_content)
            return -result if is_negative else result

        if val_type == "FLOAT_LITERAL" and val_content is not None:
            try:
                result = float(val_content)
            except ValueError:
                result = val_content
            return -result if is_negative else result

        if val_type == "STRING_LITERAL" and val_content is not None:
            return val_content

        return val_content

    def build_and_sort(self):
        """Create Callee and Caller objects from the token stream and order them.

        Scope rules:
        - program scope is the root.
        - Each function definition pushes a new named child Scope.
        - Each for-loop pushes a new anonymous block Scope so that init-clause
          declarations (e.g. 'int i = 0') are scoped to the loop, not the
          enclosing function. The scope is named 'for_<pos>' to be unique.
        - Plain if/while bodies share the enclosing scope (no new scope pushed).
        - Scopes are popped on the RBRACE that closes them, tracked via the
          parallel is_block_scope stack.
        """
        program = Scope("program")
        scope_stack = [program]
        # Parallel stack: True if the matching RBRACE should pop a scope.
        # Index 0 = program level, never popped.
        is_block_scope = [False]

        def current_scope():
            return scope_stack[-1]

        self._order = {}

        def obj_key(name, scope):
            return f"{scope.name}::{name}"

        def _type(tok):
            if tok is None:
                return None
            if isinstance(tok, tuple):
                return tok[0]
            return getattr(tok, "type", None)

        def _value(tok):
            if tok is None:
                return None
            if isinstance(tok, tuple):
                return tok[1]
            return getattr(tok, "value", getattr(tok, "lexeme", None))

        TYPE_KEYWORDS = (
            "IF", "ELSE", "WHILE", "FOR", "RETURN", "BREAK", "CONTINUE",
            "SWITCH", "CASE", "DEFAULT", "DO", "GOTO",
            "INT", "CHAR", "VOID", "FLOAT", "DOUBLE", "SHORT", "LONG",
            "SIGNED", "UNSIGNED", "STRUCT", "UNION", "ENUM", "TYPEDEF",
            "STATIC", "CONST", "VOLATILE", "EXTERN", "INLINE", "REGISTER",
            "AUTO", "SIZEOF", "RESTRICT", "BOOLEAN",
        )

        while True:
            cur = self.peek()
            if cur is None:
                break

            typ = _type(cur)

            # ----------------------------------------------------------
            # RBRACE: pop scope only if this brace opened one
            # ----------------------------------------------------------
            if typ == "RBRACE":
                self.advance()
                if len(scope_stack) > 1 and is_block_scope[-1]:
                    scope_stack.pop()
                    is_block_scope.pop()
                elif len(is_block_scope) > 1:
                    # brace that didn't open a scope — just remove tracking entry
                    is_block_scope.pop()
                continue

            # ----------------------------------------------------------
            # LBRACE not preceded by a function/for definition:
            # e.g. bare if/while body. Track it but don't push a scope.
            # ----------------------------------------------------------
            if typ == "LBRACE":
                self.advance()
                is_block_scope.append(False)
                continue

            # ----------------------------------------------------------
            # FOR loop: push a new block scope covering the init clause
            # and the loop body. Skip the header (LPAREN...RPAREN) so the
            # init tokens are processed normally inside the new scope.
            # The loop body LBRACE is consumed here; its RBRACE will pop
            # this scope via the is_block_scope stack.
            # ----------------------------------------------------------
            if typ == "FOR":
                self.advance()  # consume 'for'
                for_scope = Scope(f"for_{self.pos}", current_scope())
                scope_stack.append(for_scope)
                is_block_scope.append(True)
                # Skip '('
                if _type(self.peek()) == "LPAREN":
                    self.advance()
                # Now let the main loop process the init clause tokens
                # (they will be seen as normal type-keyword or identifier
                # statements within the new for_scope).
                continue

            # ----------------------------------------------------------
            # SEMICOLON inside for-header: separates init / cond / post.
            # Just advance; the expressions are not yet evaluated by the
            # structurer, only declarations matter here.
            # ----------------------------------------------------------
            if typ == "SEMICOLON":
                self.advance()
                continue

            # ----------------------------------------------------------
            # RPAREN: end of for-header. The next token should be LBRACE
            # (the loop body); consume RPAREN here and let LBRACE be
            # handled in the next iteration (it will append False to
            # is_block_scope since the scope was already pushed for FOR).
            # ----------------------------------------------------------
            if typ == "RPAREN":
                self.advance()
                continue

            # ----------------------------------------------------------
            # Type-keyword-led declarations: int x = -500;
            # Also detects function definitions: int main() { ...
            # ----------------------------------------------------------
            if typ in TYPE_KEYWORDS:
                self.advance()
                name_tok = self.peek()
                if _type(name_tok) == "IDENTIFIER":
                    name = _value(name_tok)
                    self.advance()  # consume identifier

                    # Function definition: int main() {
                    if _type(self.peek()) == "LPAREN":
                        self.advance()  # consume '('
                        depth = 1
                        while depth > 0:
                            t = self.peek()
                            if t is None:
                                break
                            if _type(t) == "LPAREN":
                                depth += 1
                            elif _type(t) == "RPAREN":
                                depth -= 1
                            self.advance()
                        func_callee = Callee(name, current_scope(), None)
                        key = obj_key(name, current_scope())
                        self.objects[key] = func_callee
                        self._order.setdefault(key, self.pos)
                        # Push a new function scope; its LBRACE is consumed here
                        if _type(self.peek()) == "LBRACE":
                            self.advance()  # consume '{'
                            func_scope = Scope(name, current_scope())
                            scope_stack.append(func_scope)
                            is_block_scope.append(True)
                        continue

                    # Variable declaration with optional initializer
                    if self.match("ASSIGN"):
                        value_tokens = []
                        while True:
                            nxt_tok = self.peek()
                            if nxt_tok is None or _type(nxt_tok) == "SEMICOLON":
                                break
                            self.advance()
                            value_tokens.append(nxt_tok)
                        assigned_value = self._parse_literal_value(value_tokens, _type, _value)
                        var_callee = Callee(name, current_scope(), assigned_value)
                        key = obj_key(name, current_scope())
                        self.objects[key] = var_callee
                        self._order.setdefault(key, self.pos)
                        if _type(self.peek()) == "SEMICOLON":
                            self.advance()
                    else:
                        var_callee = Callee(name, current_scope(), None)
                        key = obj_key(name, current_scope())
                        self.objects[key] = var_callee
                        self._order.setdefault(key, self.pos)
                continue

            # ----------------------------------------------------------
            # Identifier-led statements
            # ----------------------------------------------------------
            if typ == "IDENTIFIER":
                name = _value(cur)
                self.advance()
                nxt = self.peek()
                nxt_type = _type(nxt)

                if nxt_type == "ASSIGN":
                    self.advance()  # consume '='
                    value_tokens = []
                    while True:
                        nxt_tok = self.peek()
                        if nxt_tok is None or _type(nxt_tok) == "SEMICOLON":
                            break
                        self.advance()
                        value_tokens.append(nxt_tok)
                    assigned_value = self._parse_literal_value(value_tokens, _type, _value)
                    var_callee = Callee(name, current_scope(), assigned_value)
                    key = obj_key(name, current_scope())
                    self.objects[key] = var_callee
                    self._order.setdefault(key, self.pos)
                    if _type(self.peek()) == "SEMICOLON":
                        self.advance()
                    continue

                if nxt_type == "LPAREN":
                    self.advance()  # consume '('
                    args = self.collect_args()
                    lookup_key = obj_key(name, current_scope())
                    callee_node = self.objects.get(lookup_key)
                    if callee_node is None:
                        program_key = obj_key(name, program)
                        callee_node = self.objects.get(program_key)
                    if callee_node is None:
                        callee_node = Callee(name, current_scope(), None)
                        lookup_key = obj_key(name, current_scope())
                        self.objects[lookup_key] = callee_node
                        self._order.setdefault(lookup_key, self.pos)
                    caller_name = f"call_{name}_{self.pos}"
                    caller_node = Caller(caller_name, current_scope())
                    caller_node.call(callee_node, *args)
                    caller_key = obj_key(caller_name, current_scope())
                    self.objects[caller_key] = caller_node
                    self._order.setdefault(caller_key, self.pos)
                    if _type(self.peek()) == "SEMICOLON":
                        self.advance()
                    continue

                continue
            else:
                self.advance()

        # Link each Caller to its callee's children/objects.
        for obj in self.objects.values():
            if isinstance(obj, Caller):
                if obj.dependencies:
                    callee_node = obj.dependencies[0][0]
                    obj.callee_children = {
                        "callees": callee_node.scope.callees,
                        "callers": callee_node.scope.callers,
                        "generic": callee_node.scope.children,
                    }

        sorted_keys = sorted(self._order.keys(), key=lambda k: self._order[k])
        return [self.objects[k] for k in sorted_keys]


if __name__ == "__main__":
    main = Scope("main")
    stdio = Lib("stdio", main)

    def double(x):
        print(f"double called with {x}")
        return x * 2

    printf = Callee("printf", stdio.scope, double)

    x = Callee("x", main, 5)
    y = Caller("y", main, 3)

    y.call(x)
    y.call(printf, x)

    print("y.eval() =", y.eval())  # 3 + 5 + 10 = 18
