import parser as p
class Scope:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.children = {}  # name  node

    def add_child(self, node):
        # avoid accidental overwrites of children
        if node.name in self.children:
            raise ValueError(f"Child named `{node.name}` already exists in scope `{self.name}`")
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
            resolved_args = [arg.eval() if isinstance(arg, Node) else arg for arg in args]
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
                    raise NameError(f"Unknown callee `{node}` in scope `{self.scope.name}`")
            # evaluate any Node arguments before forwarding
            resolved_args = [a.eval() if isinstance(a, Node) else a for a in args]
            result += target.eval(*resolved_args)
        return result


class Lib:
    """Library scope containing callable or value nodes."""
    def __init__(self, name, parent_scope=None):
        self.name = name
        self.scope = Scope(name, parent=parent_scope)
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
    """Used for automatically structuring each line of code."""
    def __init__(self, tokens_array, scope):
        self.tokens = tokens_array
        self.pos = 0
        self.objects = {}
        self.scope = scope

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def match(self, *types):
        tok = self.peek()
        if tok and tok.type in types:
            return self.advance()
        return None

    def structure(self):
        while self.peek():
            self.statement()

    def statement(self):
        # Assignment / object creation
        if (self.peek().type == "NAME" and
                self.tokens[self.pos + 1].type == "EQUALS" and
                self.tokens[self.pos + 2].type == "NAME"):
            var_name = self.advance().value
            self.advance()  # skip '='
            class_name = self.advance().value
            self.advance()  # skip '('
            args = self.collect_args()

            if class_name == "Callee":
                obj = Callee(args[0], self.scope, args[2])
            elif class_name == "Caller":
                obj = Caller(args[0], self.scope, args[2])
            else:
                raise SyntaxError("Unknown class")

            self.objects[var_name] = obj
            return

        if self.peek().type == "NAME" and self.tokens[self.pos + 1].type == "DOT":
            caller_name = self.advance().value
            self.advance()
            method = self.advance().value
            self.advance()
            callee_name = self.advance().value
            self.advance()

            if method == "call":
                caller = self.objects[caller_name]
                callee = self.objects[callee_name]
                caller.call(callee)
            return

        self.advance()

    # Argument collecting
    def collect_args(self):
        args = []
        while self.peek().type != "RPAREN":
            tok = self.advance()
            if tok.type in ("STRING", "NUMBER", "NAME"):
                args.append(tok.value.strip('"\''))
        self.advance()
        return args

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
    y.call(x)                  # y depends on x
    y.call(printf, x)          # y calls printf with x as argument

    print("y.eval() =", y.eval())  # 3 + 5 + 10 = 18