# Vendored viewer libraries

Browser builds that `build.py` inlines into `out/code-map.html`, so the map works
offline and this repo needs no JavaScript build step.

| File | Library | Version | License |
| --- | --- | --- | --- |
| `react.production.min.js` | React (`umd/`) | 18.3.1 | MIT, `LICENSE.react` |
| `react-dom.production.min.js` | ReactDOM (`umd/`) | 18.3.1 | MIT, `LICENSE.react` |
| `jsx-runtime-shim.js` | our `jsxRuntime` global for the React Flow bundle | – | this repo |
| `xyflow-react.umd.js`, `xyflow-react.css` | React Flow (`@xyflow/react`, `dist/umd/index.js`, `dist/style.css`) | 12.12.1 | MIT, `LICENSE.xyflow` |
| `elk.bundled.js` | ELK, the Eclipse Layout Kernel (`elkjs`, `lib/elk.bundled.js`) | 0.12.1 | EPL-2.0 OR GPL-3.0-or-later, `LICENSE.elkjs.md` |

## Notes

- React Flow 13 publishes no UMD build; staying on 12.x avoids a bundler.
- React 18 has no browser build of `react/jsx-runtime`, which the React Flow
  bundle expects as the global `jsxRuntime`; the shim maps it onto
  `React.createElement`.
- Load order matters and lives in `VENDOR_SCRIPTS` in `build.py`: React,
  ReactDOM, the shim, React Flow, ELK.

## Updating

```bash
mkdir /tmp/vendor && cd /tmp/vendor
npm pack react@18.3.1 react-dom@18.3.1 @xyflow/react@12.12.1 elkjs@0.12.1
for f in *.tgz; do mkdir "${f%.tgz}" && tar -xzf "$f" -C "${f%.tgz}"; done
```

Copy the files listed above over the ones here (and the license files), keep
the order in `VENDOR_SCRIPTS`, then run the tests and look at all three views.
