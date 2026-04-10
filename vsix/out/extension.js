"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.deactivate = void 0;
const vscode = __importStar(require("vscode"));
const KEYWORDS = [
    'if', 'else', 'while', 'for', 'do', 'switch', 'case', 'default', 'break', 'continue', 'return', 'goto',
    'const', 'volatile', 'static', 'extern', 'inline', 'register', 'autoremove', 'allocate', 'free',
    'using', 'from', 'expose', 'space', 'as', 'lib', 'plstd',
    'typed', 'struct', 'init', 'end', 'self', 'lamb',
    'asm', 'sizeof', 'dynam', 'auto', 'tuple', 'string',
    'int', 'char', 'void', 'float', 'double', 'short', 'long', 'signed', 'unsigned', 'struct', 'union', 'enum'
];
const KEYWORD_INFO = {
    'using': 'Import a library or symbol. Variants: using "plstd/module", using sym from <plstd>, using sym from "file", using scope&var (intra-file immutable ref). The compiler auto-imports what you use — manual using is optional style.',
    'from': 'Specifies the source library in a using import: using printd from <plstd>, using helper from "utils".',
    'expose': 'Globalizes a library or namespace so its symbols are directly accessible without a prefix. Variants: expose plstd, expose lib@module, expose mySpace.',
    'space': 'Declares a named namespace block inside a .plib library file. Members are accessed via the @ operator (e.g. func@mySpace).',
    'as': 'Alias a symbol at the point of import: using printd as pd from <plstd>.',
    'lib': 'Refers to the plstd standard library.',
    'plstd': 'The C△ standard library. Contains printd, printfs, len, and more.',
    'typed': '(future) Declares a struct that becomes a native type with inheritance support via &. e.g. typed struct Dog&Animal.',
    'struct': 'Declares a plain struct with constructors and member functions but no inheritance.',
    'init': 'Struct constructor lifecycle function — runs when an instance is created.',
    'end': 'Struct destructor lifecycle function — runs when an instance is freed or goes out of scope.',
    'self': 'Optional self-reference inside struct member functions and lifecycle blocks.',
    'lamb': '(future) Declares a named lambda with typed parameters. Always named, never anonymous.',
    'dynam': '(future) Dynamic array type supporting .push(), .pop(), .remove(index). Sized by len().',
    'auto': '(future) Dynamic/inferred type resolved at compile time via the simulation pass. Use %k format specifier with printd.',
    'tuple': '(future) Heterogeneous list type declared with [] syntax. Sized by len().',
    'string': '(future) First-class string type supporting + concatenation and {expr} f-string interpolation.',
    'autoremove': 'Heap-allocates a value that is automatically freed by the compiler at its last point of use (simulation pass). Supports the robbery ownership pattern.',
    'allocate': 'Heap-allocates a named value: allocate int buf[64], allocate int x(200). Pair with free or use autoremove.',
    'free': 'Manually releases heap memory allocated with allocate.',
    'asm': 'Declares an inline assembly function or block. Compiled by NASM and linked into the binary. Uses syntax x86_64_linux. return replaces ret; implicit return is rax.',
    'printd': 'plstd type-aware print function. Uses printf-style format specifiers. Use %k for auto-typed values.',
    'printfs': 'plstd f-string print function. Embeds expressions using {expr} syntax.',
    'len': 'Built-in language function returning the length of a string, dynam array, or tuple. No import needed.',
    'restrict': 'REMOVED in C△ — raises SyntaxError.',
    '_Bool': 'REMOVED in C△ — raises SyntaxError.',
    '_Complex': 'REMOVED in C△ — raises SyntaxError.',
    '_Imaginary': 'REMOVED in C△ — raises SyntaxError.',
};
function activate(context) {
    const selector = { language: 'c△', scheme: 'file' };
    const completionProvider = vscode.languages.registerCompletionItemProvider(selector, {
        provideCompletionItems(document, position) {
            const word = document.getText(new vscode.Range(position.translate(0, -1), position));
            const items = [];
            for (const kw of KEYWORDS) {
                if (kw.startsWith(word.toLowerCase())) {
                    const item = new vscode.CompletionItem(kw, vscode.CompletionItemKind.Keyword);
                    if (KEYWORD_INFO[kw]) {
                        item.detail = KEYWORD_INFO[kw];
                    }
                    items.push(item);
                }
            }
            return items;
        }
    }, ...['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']);
    const hoverProvider = vscode.languages.registerHoverProvider(selector, {
        provideHover(document, position) {
            const range = document.getWordRangeAtPosition(position, /[A-Za-z_][A-Za-z0-9_]*/);
            if (!range)
                return null;
            const word = document.getText(range);
            const info = KEYWORD_INFO[word] || KEYWORD_INFO[word.toLowerCase()];
            if (!info)
                return null;
            const md = new vscode.MarkdownString(`**\`${word}\`** — ${info}`);
            md.isTrusted = true;
            return new vscode.Hover(md, range);
        }
    });
    const diagnosticCollection = vscode.languages.createDiagnosticCollection('ctriangle');
    const lintDocument = (document) => {
        if (!document.fileName.endsWith('.ctri') && !document.fileName.endsWith('.plib'))
            return;
        const diagnostics = [];
        const lines = document.getText().split('\n');
        let inBlockComment = false;
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line.startsWith('/*'))
                inBlockComment = true;
            if (inBlockComment) {
                if (line.includes('*/'))
                    inBlockComment = false;
                continue;
            }
            if (line.startsWith('//') || line === '')
                continue;
            const deprecated = ['restrict', '_Bool', '_Complex', '_Imaginary'];
            for (const kw of deprecated) {
                const idx = line.indexOf(kw);
                if (idx !== -1) {
                    diagnostics.push(new vscode.Diagnostic(new vscode.Range(i, idx, i, idx + kw.length), `'${kw}' is removed in C△ and will raise a SyntaxError`, vscode.DiagnosticSeverity.Error));
                }
            }
            if (line.includes('using') && line.includes('<') && !line.includes('>')) {
                diagnostics.push(new vscode.Diagnostic(new vscode.Range(i, line.indexOf('<'), i, line.length), 'Missing closing > in import', vscode.DiagnosticSeverity.Error));
            }
        }
        diagnosticCollection.set(document.uri, diagnostics);
    };
    context.subscriptions.push(completionProvider, hoverProvider, diagnosticCollection, vscode.workspace.onDidOpenTextDocument(lintDocument), vscode.workspace.onDidChangeTextDocument(e => lintDocument(e.document)), vscode.workspace.onDidSaveTextDocument(lintDocument));
    vscode.workspace.textDocuments.forEach(lintDocument);
}
function deactivate() { }
exports.deactivate = deactivate;
//# sourceMappingURL=extension.js.map
