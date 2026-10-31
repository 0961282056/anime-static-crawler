# MIT License

Copyright © 2019-2025 Caleb Porzio and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Vendored file: `alpine-csp-3.15.12.min.js`

- Package: `@alpinejs/csp` 3.15.12
- Source tarball: `https://registry.npmjs.org/@alpinejs/csp/-/csp-3.15.12.tgz`
- npm tarball integrity: `sha512-9jielHzVPqMlO9zQ6K1WtdhSHVX5OZfq5ZDUSVZfY10VnXRoRBx8nOadWjsG1xev/kz4EPaNN9eDFWtZSvJHxA==`
- Upstream file SHA-256: `566167134bb2347110904e2ced6e816d2e8d837200c158f98b72372b3bb0b9a6`
- Vendored file SHA-256 with the local storage-access guard: `6ae52782db18a68b5d26d357f8000d0c934b7d72ec99263d2bd351f07956a35a`

Local compatibility patch (2026-10-30): while collecting global object identities,
ignore an inaccessible property when its getter throws (for example, a browser
that denies access to `localStorage`). Readable globals retain the original CSP
identity checks. No other vendor code or CSP policy changes.
`tests/test_frontend_security.py` verifies the patched hash and that reversing
this one replacement reproduces the exact upstream hash:

```js
// Upstream
Object.getOwnPropertyNames(globalThis).forEach(t=>{t==="styleMedia"||t==="sharedStorage"||cn.add(globalThis[t])});
// Local guard
Object.getOwnPropertyNames(globalThis).forEach(t=>{if(t==="styleMedia"||t==="sharedStorage")return;try{cn.add(globalThis[t])}catch{}});
```
