// React 18 ships no browser build of react/jsx-runtime, which the React Flow
// UMD bundle expects as the global `jsxRuntime`. JSX calls map directly onto
// React.createElement (children travel inside props).
window.jsxRuntime = {
  jsx: (type, props, key) => React.createElement(type, key === undefined ? props : { ...props, key }),
  jsxs: (type, props, key) => React.createElement(type, key === undefined ? props : { ...props, key }),
  Fragment: React.Fragment,
};
