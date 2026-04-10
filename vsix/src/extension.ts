import * as vscode from 'vscode';

const KEYWORDS = [
    'if', 'else', 'while', 'for', 'do', 'switch', 'case', 'default', 'break', 'continue', 'return', 'goto',
    'const', 'volatile', 'static', 'extern', 'inline', 'register', 'autoremove', 'allocate', 'free',
    'using', 'from', 'expose', 'space', 'as', 'lib', 'plstd',
    'typed', 'struct', 'init', 'end', 'self', 'lamb',
    'asm', 'sizeof', 'dynam', 'auto', 'tuple', 'string',
    'int', 'char', 'void', 'float', 'double', 'short', 'long', 'signed', 'unsigned', 'struct', 'union', 'enum'
];

const KEYWORD_INFO: { [key: string]: string } = {
    'using': 'Import library: using printd from <plstd>, using helper from "utils", using scope&myVar',
    'from': 'Specify library source: from <plstd>, from "file"',
    'expose': 'Make functions accessible without namespace: expose printd@plstd',
    'space': 'Define namespace in .plib file: space math',
    'as': 'Alias in import: using printd as pd from <plstd>',
    'lib': 'Shortcut for plstd standard library',
    'plstd': 'Standard library namespace',
    'typed': 'Native type struct with inheritance support',
    'struct': 'Plain struct without inheritance',
    'init': 'Struct constructor lifecycle',
    'end': 'Struct destructor lifecycle',
    'self': 'Optional self-reference in struct member functions',
    'lamb': 'Named lambda expression',
    'dynam': 'Dynamic array type with .push(), .pop(), .remove(index)',
    'auto': 'Dynamic/inferred type resolved at runtime',
    'tuple': 'Heterogeneous list: tuple t = [1, "hello", 3.14]',
    'string': 'First-class string type with + concatenation and {expr} interpolation',
    'autoremove': 'Heap allocation freed automatically at last use',
    'allocate': 'Heap allocation: allocate int buf[64], allocate int x(200)',
    'free': 'Manual heap deallocation',
    'asm': 'Inline assembly block'
};

function getCompilerPath(): string {
    const config = vscode.workspace.getConfiguration('ctriangle');
    return config.get<string>('compilerPath') || 'hypotenuse';
}

function registerCompletionProvider(document: vscode.TextDocument, position: vscode.Position): vscode.CompletionItem[] {
    const line = document.lineAt(position.line).text;
    const word = document.getText(new vscode.Range(position.translate(0, -1), position));
    const items: vscode.CompletionItem[] = [];

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

function lintDocument(document: vscode.TextDocument): vscode.Diagnostic[] {
    const diagnostics: vscode.Diagnostic[] = [];
    const text = document.getText();
    const lines = text.split('\n');

    let inBlockComment = false;
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        if (line.startsWith('/*')) {
            inBlockComment = true;
        }
        if (inBlockComment) {
            if (line.includes('*/')) {
                inBlockComment = false;
            }
            continue;
        }

        if (line.startsWith('//') || line === '') {
            continue;
        }

        if (line.includes('using') && line.includes('<') && !line.includes('>')) {
            const diag = new vscode.Diagnostic(new vscode.Range(i, line.indexOf('<'), i, line.length), 'Missing closing >', vscode.DiagnosticSeverity.Error);
            diagnostics.push(diag);
        }

        const braceMatch = line.match(/\{/g);
        const closeMatch = line.match(/\}/g);
        if (braceMatch && closeMatch && braceMatch.length !== closeMatch.length) {
            const diag = new vscode.Diagnostic(new vscode.Range(i, 0, i, line.length), 'Unbalanced braces', vscode.DiagnosticSeverity.Warning);
            diagnostics.push(diag);
        }
    }

    return diagnostics;
}

export function activate(context: vscode.ExtensionContext) {
    const selector = { language: 'c△', scheme: 'file' };

    const completionProvider = vscode.languages.registerCompletionItemProvider(selector, {
        provideCompletionItems(document, position) {
            return registerCompletionProvider(document, position);
        }
    }, ...['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']);

    const hoverProvider = vscode.languages.registerHoverProvider(selector, {
        provideHover(document, position) {
            const range = document.getWordRangeAtPosition(position);
            if (range) {
                const word = document.getText(range);
                if (KEYWORD_INFO[word.toLowerCase()]) {
                    return new vscode.Hover(KEYWORD_INFO[word.toLowerCase()], range);
                }
            }
            return null;
        }
    });

    const diagnosticCollection = vscode.languages.createDiagnosticCollection('ctriangle');

    const lintDocumentWrapper = (document: vscode.TextDocument) => {
        if (document.fileName.endsWith('.ctri') || document.fileName.endsWith('.plib')) {
            diagnosticCollection.set(document.uri, lintDocument(document));
        }
    };

    const openListener = vscode.workspace.onDidOpenTextDocument((doc) => {
        lintDocumentWrapper(doc);
    });

    const changeListener = vscode.workspace.onDidChangeTextDocument((event) => {
        lintDocumentWrapper(event.document);
    });

    const saveListener = vscode.workspace.onDidSaveTextDocument((doc) => {
        lintDocumentWrapper(doc);
    });

    vscode.workspace.textDocuments.forEach(lintDocumentWrapper);

    context.subscriptions.push(completionProvider, hoverProvider, diagnosticCollection, openListener, changeListener, saveListener);
}

export function deactivate() {}