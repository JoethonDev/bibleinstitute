"""
Design Theme Catalog
====================
Showcase-only theme definitions for visual comparison before
refactoring the user-facing pages and the admin dashboard.

Each theme is a self-contained dictionary of design tokens:
  - id, name, tagline, mood        : identity
  - palette                         : color tokens
  - font (heading, body)            : typography
  - radius, density, shadow         : shape & depth
  - style (css)                     : opinionated CSS overrides
  - cover                           : color blocks for the catalog card

Pages consume THEME via the `theme` context variable and apply
`data-theme="{{ theme.id }}"` on the body so the embedded CSS
in theme_base.html can re-skin the entire page.
"""

THEMES = [
    {
        "id": "sapphire",
        "name": "Royal Sapphire",
        "tagline": "Heritage, gold accents, Bible Institute feel",
        "mood": "Traditional / Faith / Premium",
        "cover": ["#0b1f4d", "#1e3a8a", "#c9a227"],
        "font": {
            "heading": "'Playfair Display', Georgia, serif",
            "body": "'Inter', system-ui, sans-serif",
        },
        "palette": {
            "primary": "#1e3a8a",
            "primary_strong": "#0b1f4d",
            "primary_soft": "#e7ecff",
            "accent": "#c9a227",
            "accent_soft": "#f6ecc4",
            "surface": "#ffffff",
            "surface_alt": "#f5f4ef",
            "ink": "#0b1f4d",
            "ink_soft": "#475569",
            "border": "#e2e8f0",
            "success": "#1f7a4d",
            "warning": "#b45309",
            "danger": "#991b1b",
        },
        "radius": 6,
        "shadow": "0 1px 2px rgba(11,31,77,0.06), 0 8px 24px rgba(11,31,77,0.08)",
        "style": "classic",
    },
    {
        "id": "minimal",
        "name": "Modern Minimal",
        "tagline": "Whitespace, type, single accent",
        "mood": "Clean / Product / SaaS",
        "cover": ["#0f172a", "#ffffff", "#0ea5e9"],
        "font": {
            "heading": "'Inter', system-ui, sans-serif",
            "body": "'Inter', system-ui, sans-serif",
        },
        "palette": {
            "primary": "#0f172a",
            "primary_strong": "#020617",
            "primary_soft": "#f1f5f9",
            "accent": "#0ea5e9",
            "accent_soft": "#e0f2fe",
            "surface": "#ffffff",
            "surface_alt": "#f8fafc",
            "ink": "#0f172a",
            "ink_soft": "#475569",
            "border": "#e5e7eb",
            "success": "#059669",
            "warning": "#d97706",
            "danger": "#dc2626",
        },
        "radius": 10,
        "shadow": "0 1px 2px rgba(15,23,42,0.04), 0 10px 30px rgba(15,23,42,0.06)",
        "style": "minimal",
    },
    {
        "id": "sunset",
        "name": "Vibrant Sunset",
        "tagline": "Bold gradients, energetic, youthful",
        "mood": "Friendly / Vibrant / Modern EdTech",
        "cover": ["#7c3aed", "#ec4899", "#f59e0b"],
        "font": {
            "heading": "'Plus Jakarta Sans', 'Inter', sans-serif",
            "body": "'Inter', system-ui, sans-serif",
        },
        "palette": {
            "primary": "#7c3aed",
            "primary_strong": "#5b21b6",
            "primary_soft": "#f5f3ff",
            "accent": "#ec4899",
            "accent_soft": "#fce7f3",
            "surface": "#ffffff",
            "surface_alt": "#faf5ff",
            "ink": "#1f1147",
            "ink_soft": "#5b21b6",
            "border": "#ede9fe",
            "success": "#10b981",
            "warning": "#f59e0b",
            "danger": "#ef4444",
        },
        "radius": 14,
        "shadow": "0 1px 2px rgba(124,58,237,0.08), 0 14px 36px rgba(236,72,153,0.12)",
        "style": "vibrant",
        "gradient": "linear-gradient(135deg, #7c3aed 0%, #ec4899 50%, #f59e0b 100%)",
    },
    {
        "id": "forest",
        "name": "Forest Academic",
        "tagline": "Earthy greens, scholarly, calm",
        "mood": "Academic / Calm / Trustworthy",
        "cover": ["#14532d", "#166534", "#a16207"],
        "font": {
            "heading": "'Source Serif Pro', Georgia, serif",
            "body": "'Source Sans Pro', system-ui, sans-serif",
        },
        "palette": {
            "primary": "#166534",
            "primary_strong": "#14532d",
            "primary_soft": "#ecfdf5",
            "accent": "#a16207",
            "accent_soft": "#fef3c7",
            "surface": "#fffdf7",
            "surface_alt": "#f5f5f0",
            "ink": "#1c1917",
            "ink_soft": "#57534e",
            "border": "#e7e5e0",
            "success": "#166534",
            "warning": "#a16207",
            "danger": "#991b1b",
        },
        "radius": 4,
        "shadow": "0 1px 2px rgba(20,83,45,0.06), 0 6px 18px rgba(20,83,45,0.08)",
        "style": "classic",
    },
    {
        "id": "midnight",
        "name": "Midnight Glass",
        "tagline": "Dark mode, glassy, premium",
        "mood": "Dark / Premium / Immersive",
        "cover": ["#020617", "#1e1b4b", "#22d3ee"],
        "font": {
            "heading": "'Manrope', 'Inter', sans-serif",
            "body": "'Inter', system-ui, sans-serif",
        },
        "palette": {
            "primary": "#22d3ee",
            "primary_strong": "#0891b2",
            "primary_soft": "#083344",
            "accent": "#a78bfa",
            "accent_soft": "#2e1065",
            "surface": "#0b1020",
            "surface_alt": "#111835",
            "ink": "#e2e8f0",
            "ink_soft": "#94a3b8",
            "border": "#1e293b",
            "success": "#34d399",
            "warning": "#fbbf24",
            "danger": "#f87171",
        },
        "radius": 12,
        "shadow": "0 0 0 1px rgba(34,211,238,0.08), 0 18px 40px rgba(0,0,0,0.5)",
        "style": "dark",
    },
    {
        "id": "pastel",
        "name": "Pastel Soft",
        "tagline": "Soft, friendly, modern education",
        "mood": "Approachable / Soft / Beginner-friendly",
        "cover": ["#fde68a", "#fbcfe8", "#bae6fd"],
        "font": {
            "heading": "'Nunito', 'Inter', sans-serif",
            "body": "'Nunito', 'Inter', sans-serif",
        },
        "palette": {
            "primary": "#0e7490",
            "primary_strong": "#155e75",
            "primary_soft": "#cffafe",
            "accent": "#db2777",
            "accent_soft": "#fce7f3",
            "surface": "#ffffff",
            "surface_alt": "#f8fafc",
            "ink": "#1f2937",
            "ink_soft": "#6b7280",
            "border": "#e5e7eb",
            "success": "#059669",
            "warning": "#d97706",
            "danger": "#e11d48",
        },
        "radius": 18,
        "shadow": "0 2px 6px rgba(14,116,144,0.06), 0 12px 28px rgba(219,39,119,0.08)",
        "style": "soft",
    },
]


def get_theme(theme_id):
    for theme in THEMES:
        if theme["id"] == theme_id:
            return theme
    return THEMES[0]


def theme_choices():
    return [(t["id"], t["name"]) for t in THEMES]


# Showcase pages available for each theme. Each renders the
# main user-side surface or the admin dashboard with the theme
# applied, using static sample data so the user can compare
# visual decisions without needing to log in.
SHOWCASE_PAGES = [
    {"id": "home", "name": "Home", "icon": "fa-house", "group": "User Side"},
    {"id": "courses", "name": "Courses List", "icon": "fa-book-open", "group": "User Side"},
    {"id": "course_detail", "name": "Course Detail", "icon": "fa-circle-play", "group": "User Side"},
    {"id": "profile", "name": "Profile", "icon": "fa-id-badge", "group": "User Side"},
    {"id": "admin", "name": "Admin Dashboard", "icon": "fa-gauge-high", "group": "Admin"},
    {"id": "forms", "name": "Form Components", "icon": "fa-rectangle-list", "group": "Tools"},
    {"id": "quiz", "name": "Quiz Experience", "icon": "fa-pen-to-square", "group": "Tools"},
    {"id": "states", "name": "Error & Empty States", "icon": "fa-circle-exclamation", "group": "Tools"},
    {"id": "editor", "name": "Live Theme Editor", "icon": "fa-sliders", "group": "Tools"},
]
