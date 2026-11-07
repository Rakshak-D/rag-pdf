# Opening PDF sources

Documents offers a **Preview** action for each imported PDF. The viewer reads the original bytes from the authenticated local bootstrap API and checks the current source manifest and document digest before returning them. A source that has changed since the list or citation was created must be refreshed and reopened; the viewer never substitutes another PDF.

Document Chat citations can open the matching source at the returned page. Page navigation and zoom run locally in the browser. Citation passages are not highlighted because retrieval does not return verified page coordinates. A restored answer whose evidence has expired cannot open its old source link.

The viewer accepts complete PDFs up to 16 MiB and 200 pages. It does not request HTTP ranges or persist document bytes. The gateway permits PDF content only on the exact authenticated source route; browser code receives no backend credential. PDF.js scripting, XFA, WebAssembly, range fetching and annotation actions are disabled. If the source changes, disappears, exceeds the limit or cannot be rendered, the viewer reports the failure and provides a return path.
