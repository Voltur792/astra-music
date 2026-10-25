"""Navigation icon: headphones with Astra's four-point star, as on the store icon.

Astra 0.2.7 draws only vector tab icons; a PNG embedded through <image> leaves
the tab blank, so this is plain SVG that follows the theme via currentColor.
"""

TAB_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/>'
    '<rect x="2.5" y="13" width="4" height="7" rx="2"/>'
    '<rect x="17.5" y="13" width="4" height="7" rx="2"/>'
    '<path d="M12 8.5l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z"/></svg>'
)
