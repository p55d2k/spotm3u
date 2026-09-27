/**
 * The platform this bundle was built for.
 *
 * Rewritten at build time by ``packaging/stamp_version.py``, the same way the
 * release tag rewrites ``spotm3u.__version__``, so the frontend learns the
 * target the way the packaged application does rather than by asking the WebView
 * at runtime and trusting that the answer arrived in time. pywebview injects the
 * bridge on its own thread, so a first render can happen before it exists, and
 * it names its WebView backend rather than the operating system.
 *
 * The application is built per platform and cross-compilation is not supported,
 * so this is the authoritative answer for the window frame. The bridge is
 * kept as a corroborating source, not the only one.
 *
 * Empty means the bundle was not stamped. An unknown value is not a platform:
 * it draws no window controls until the bridge reports one. Kept as a single
 * plain source assignment so stamping has exactly one place to rewrite.
 */
export const BUILD_TARGET_PLATFORM = "";
