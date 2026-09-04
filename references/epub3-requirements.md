# EPUB3 requirements

Generated books must contain an uncompressed first `mimetype` entry, `META-INF/container.xml`, an EPUB3 OPF package, XHTML content, navigation, manifest, and spine. All reading assets must be local. EPUBCheck errors fail conversion; missing EPUBCheck produces a warning and internal ZIP/package validation still runs. CSS must remain reflowable, images responsive, tables bounded, and code wrappable.
