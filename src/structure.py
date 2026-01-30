class Scope:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.children = {}  # name → node

    def add_child(self, node):
        # avoid accidental overwrites of children
        if node.name in self.children:
            raise ValueError(
                f"Child named `{node.name}` already exists in scope `{self.name}`"
            )
        self.children[node.name] = node
        return node

    def called(self, name):
        if name in self.children:
            return self.children[name]
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

    def eval(self, *args, **kwargs):
        # If value is callable, call it with resolved args.
        if callable(self.value):
            resolved_args = [
                arg.eval() if isinstance(arg, Node) else arg for arg in args
            ]
            return self.value(*resolved_args)
        # If value is a Node, evaluate it and return its value.
        if isinstance(self.value, Node):
            return self.value.eval()
        # Otherwise return the literal value.
        return self.value


class Caller(Node):
    """Node that can depend on other nodes and call function nodes."""

    def __init__(self, name, scope, value=None):
        super().__init__(name, scope)
        self.value = value

    def call(self, node, *args):
        """Depend on a node. Arguments can be nodes or literals."""
        self.dependencies.append((node, args))

    def eval(self):
        # Evaluate self.value if it's a Node, otherwise start with numeric value or 0.
        if isinstance(self.value, Node):
            result = self.value.eval()
        else:
            result = self.value if isinstance(self.value, (int, float)) else 0

        for node, args in self.dependencies:
            target = node
            # allow dependencies to be recorded by name (string); resolve via scope
            if isinstance(node, str):
                target = self.scope.called(node)
                if target is None:
                    raise NameError(
                        f"Unknown callee `{node}` in scope `{self.scope.name}`"
                    )
            # evaluate any Node arguments before forwarding
            resolved_args = [a.eval() if isinstance(a, Node) else a for a in args]
            result += target.eval(*resolved_args)
        return result


class Lib:
    """Library scope containing callable or value nodes."""

    def __init__(self, name, parent_scope=None):
        self.name = name
        self.scope = Scope(name, parent_scope)
        if parent_scope:
            parent_scope.add_child(self.scope)

    def add_node(self, node):
        # Node constructors already register themselves with the scope.
        # Avoid attempting to add the same node twice.
        if node.name in self.scope.children:
            return self.scope.children[node.name]
        return self.scope.add_child(node)

    def called(self, name):
        return self.scope.called(name)


class Structor:
    """Automatically structures each line of code.

    The parser implementation is injected via the ``parser`` argument to the
    constructor, removing the need for a hard‑coded import.
    """

    def __init__(self, tokens_array, parser):
        self.tokens = tokens_array
        self.pos = 0
        self.objects = {}
        self.parser = parser  # injected parser module/object

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def match(self, *types):
        tok = self.peek()
        if tok and getattr(tok, "type", None) in types:
            return self.advance()
        return None

    # Function Argument collecting
    def collect_args(self):
        """Collect function‑call arguments and return a list of parsed AST nodes.

        Tokens are gathered until a matching RPAREN is found. The raw token
        list for each argument (separated by commas) is fed to the injected
        parser's ``Parser`` class and ``parse_expression`` is invoked, so the
        caller receives fully parsed expression objects rather than raw strings.
        """
        raw_args = []  # List of token lists, one per argument
        current = []
        while self.peek() is not None and self.peek() != "RPAREN":
            tok = self.advance()
            # ``tok`` may be a tuple (type, lexeme) or a token object.
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
        # Consume the closing RPAREN.
        if self.peek() == "RPAREN":
            self.advance()
        # Parse each argument token slice into an AST node using the injected parser.
        parsed_args = []
        for arg_tokens in raw_args:
            if not arg_tokens:
                continue
            parser_instance = self.parser.Parser(arg_tokens)
            parsed_args.append(parser_instance.parse_expression())
        return parsed_args

    def build_and_sort(self):
        """Create Callee and Caller objects from the token stream and order them.

        This method walks the token list, uses the full parser only for
        expressions and function‑call arguments, and builds lightweight
        ``Callee``/``Caller`` nodes. The resulting objects are returned sorted
        by their original appearance (pointer‑line order).
        """
        # Global scope that will contain the nodes.
        program = Scope("program")
        # Mapping of name -> first appearance index for sorting later.
        self._order = {}

        # Helper functions to safely extract token type/value regardless of
        # representation (tuple, object, or None).
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

        while True:
            cur = self.peek()
            if cur is None:
                break

            typ = _type(cur)
            val = _value(cur)

            if typ == "IDENTIFIER":
                name = val
                self.advance()  # consume identifier
                nxt = self.peek()
                nxt_type = _type(nxt)

                # -------------------------------------------------
                # 1. Variable/value definition: IDENTIFIER ASSIGN expr SEMICOLON
                # -------------------------------------------------
                if nxt_type == "ASSIGN":
                    self.advance()  # consume '='
                    expr_tokens = []
                    while True:
                        nxt_tok = self.peek()
                        if nxt_tok is None or _type(nxt_tok) == "SEMICOLON":
                            break
                        expr_tokens.append(self.advance())
                    # Consume trailing semicolon if present.
                    if _type(self.peek()) == "SEMICOLON":
                        self.advance()
                    # Parse the expression using the injected parser.
                    expr_ast = self.parser.Parser(expr_tokens).parse_expression()
                    callee_node = Callee(name, program, expr_ast)
                    self.objects[name] = callee_node
                    self._order.setdefault(name, self.pos)
                    continue

                # -------------------------------------------------
                # 2. Function‑like call: IDENTIFIER LPAREN ... RPAREN
                # -------------------------------------------------
                if nxt_type == "LPAREN":
                    self.advance()  # consume '('
                    args = self.collect_args()  # returns parsed AST nodes
                    # Resolve (or lazily create) the callee.
                    callee_node = self.objects.get(name)
                    if callee_node is None:
                        callee_node = Callee(name, program, None)
                        self.objects[name] = callee_node
                        self._order.setdefault(name, self.pos)
                    # Create a Caller representing this invocation.
                    caller_name = f"call_{name}_{self.pos}"
                    caller_node = Caller(caller_name, program)
                    caller_node.call(callee_node, *args)
                    self.objects[caller_name] = caller_node
                    self._order.setdefault(caller_name, self.pos)
                    # Optional trailing semicolon.
                    if _type(self.peek()) == "SEMICOLON":
                        self.advance()
                    continue

                # Any other identifier usage is ignored for structuring purposes.
                continue
            else:
                # Non‑identifier tokens are ignored.
                self.advance()

        # Return objects sorted by their first appearance (pointer order).
        sorted_names = sorted(self._order.keys(), key=lambda k: self._order[k])
        return [self.objects[n] for n in sorted_names]


if __name__ == "__main__":
    main = Scope("main")
    stdio = Lib("stdio", main)

    # First-class function
    def double(a):
        print(f"double called with {a}")
        return a * 2

    printf = Callee("printf", stdio.scope, double)

    # Values
    x = Callee("x", main, 5)
    y = Caller("y", main, 3)

    # Dependencies
    y.call(x)  # y depends on x
    y.call(printf, x)  # y calls printf with x as argument

    print("y.eval() =", y.eval())  # 3 + 5 + 10 = 18
