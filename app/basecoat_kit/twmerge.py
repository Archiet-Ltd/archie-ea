"""A Python port of the parts of tailwind-merge that shadcn/ui relies on.

``cn()`` upstream is clsx + tailwind-merge: when a caller passes ``className="w-fit"`` to a component whose
recipe says ``w-full``, the recipe class is dropped. Our macros must do the same or the rendered class set
(and so the computed style) diverges from upstream. Classes are keyed by (variant prefix, utility group);
the last class in a group wins, and a few groups also clear their sub-groups (``p-*`` clears ``px-*`` ...).
"""
import re

_COLOR_WORDS = r'(?:inherit|current|transparent|black|white|background|foreground|card|card-foreground|popover|popover-foreground|primary|primary-foreground|secondary|secondary-foreground|muted|muted-foreground|accent|accent-foreground|destructive|destructive-foreground|border|input|ring|chart-\d|sidebar(?:-[a-z-]+)?|surface|surface-foreground|selection|selection-foreground|code|code-foreground|code-highlight|code-number|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})'
_SIZE_WORDS = r'(?:xs|sm|base|lg|xl|\dxl|\[[^\]]+\]|\([^)]+\))'

GROUPS = [
    # (regex on the base class, group name)
    (r'^(?:block|inline-block|inline|flex|inline-flex|grid|inline-grid|contents|table|table-row|table-cell|flow-root|list-item|hidden)$', 'display'),
    (r'^(?:static|fixed|absolute|relative|sticky)$', 'position'),
    (r'^(?:visible|invisible|collapse)$', 'visibility'),
    (r'^(?:flex-row|flex-col|flex-row-reverse|flex-col-reverse)$', 'flex-direction'),
    (r'^(?:flex-wrap|flex-nowrap|flex-wrap-reverse)$', 'flex-wrap'),
    (r'^(?:flex-1|flex-auto|flex-initial|flex-none|flex-\[.*\]|flex-\(.*\))$', 'flex'),
    (r'^grow(?:-.*)?$', 'grow'), (r'^shrink(?:-.*)?$', 'shrink'), (r'^basis-', 'basis'), (r'^order-', 'order'),
    (r'^size-', 'size'), (r'^min-w-', 'min-w'), (r'^max-w-', 'max-w'), (r'^w-', 'w'), (r'^min-h-', 'min-h'), (r'^max-h-', 'max-h'), (r'^h-', 'h'),
    (r'^p-', 'p'), (r'^px-', 'px'), (r'^py-', 'py'), (r'^pt-', 'pt'), (r'^pr-', 'pr'), (r'^pb-', 'pb'), (r'^pl-', 'pl'), (r'^ps-', 'ps'), (r'^pe-', 'pe'),
    (r'^-?m-', 'm'), (r'^-?mx-', 'mx'), (r'^-?my-', 'my'), (r'^-?mt-', 'mt'), (r'^-?mr-', 'mr'), (r'^-?mb-', 'mb'), (r'^-?ml-', 'ml'), (r'^-?ms-', 'ms'), (r'^-?me-', 'me'),
    (r'^gap-x-', 'gap-x'), (r'^gap-y-', 'gap-y'), (r'^gap-', 'gap'), (r'^-?space-x-', 'space-x'), (r'^-?space-y-', 'space-y'),
    (r'^items-', 'items'), (r'^justify-items-', 'justify-items'), (r'^justify-self-', 'justify-self'), (r'^justify-', 'justify'), (r'^self-', 'self'), (r'^content-', 'content'), (r'^place-items-', 'place-items'), (r'^place-content-', 'place-content'), (r'^place-self-', 'place-self'),
    (r'^grid-cols-', 'grid-cols'), (r'^grid-rows-', 'grid-rows'), (r'^col-span-', 'col-span'), (r'^col-start-', 'col-start'), (r'^col-end-', 'col-end'), (r'^row-span-', 'row-span'), (r'^row-start-', 'row-start'), (r'^row-end-', 'row-end'), (r'^auto-rows-', 'auto-rows'), (r'^auto-cols-', 'auto-cols'), (r'^grid-flow-', 'grid-flow'),
    (r'^(?:inset-x-|-inset-x-)', 'inset-x'), (r'^(?:inset-y-|-inset-y-)', 'inset-y'), (r'^-?inset-', 'inset'), (r'^-?top-', 'top'), (r'^-?right-', 'right'), (r'^-?bottom-', 'bottom'), (r'^-?left-', 'left'), (r'^-?start-', 'start'), (r'^-?end-', 'end'), (r'^-?z-', 'z'),
    (r'^text-(?:left|center|right|justify|start|end)$', 'text-align'),
    (r'^text-(?:ellipsis|clip|wrap|nowrap|balance|pretty)$', 'text-wrap'),
    (r'^text-' + _SIZE_WORDS + r'(?:/.*)?$', 'font-size'),
    (r'^text-' + _COLOR_WORDS + r'(?:/.*)?$', 'text-color'),
    (r'^text-\[', 'font-size'),
    (r'^font-(?:thin|extralight|light|normal|medium|semibold|bold|extrabold|black|\[.*\])$', 'font-weight'),
    (r'^font-(?:sans|serif|mono|heading)$', 'font-family'),
    (r'^(?:italic|not-italic)$', 'font-style'), (r'^leading-', 'leading'), (r'^tracking-', 'tracking'), (r'^whitespace-', 'whitespace'), (r'^break-', 'break'), (r'^(?:truncate)$', 'truncate'), (r'^line-clamp-', 'line-clamp'), (r'^(?:uppercase|lowercase|capitalize|normal-case)$', 'text-transform'), (r'^(?:underline|overline|line-through|no-underline)$', 'text-decoration'), (r'^underline-offset-', 'underline-offset'), (r'^decoration-', 'decoration'), (r'^align-', 'vertical-align'), (r'^list-', 'list'),
    (r'^bg-(?:linear|radial|conic|gradient|none)', 'bg-image'), (r'^bg-(?:cover|contain|auto)$', 'bg-size'), (r'^bg-(?:center|top|bottom|left|right)', 'bg-position'), (r'^bg-(?:repeat|no-repeat)', 'bg-repeat'), (r'^bg-clip-', 'bg-clip'), (r'^bg-', 'bg-color'),
    (r'^from-', 'from'), (r'^via-', 'via'), (r'^to-', 'to'),
    (r'^rounded-(?:t|b|l|r|s|e|tl|tr|bl|br|ss|se|es|ee)(?:-|$)', None), (r'^rounded(?:-|$)', 'rounded'),
    (r'^border-(?:solid|dashed|dotted|double|hidden|none)$', 'border-style'),
    (r'^border-(?:x|y|t|r|b|l|s|e)(?:-\d+)?$', None), (r'^border(?:-\d+|-\[.*\])?$', 'border-w'),
    (r'^border-(?:x|y|t|r|b|l|s|e)-' + _COLOR_WORDS, None), (r'^border-' + _COLOR_WORDS + r'(?:/.*)?$', 'border-color'),
    (r'^divide-', 'divide'), (r'^outline-(?:none|hidden|solid|dashed|dotted|double)$', 'outline-style'), (r'^outline-offset-', 'outline-offset'), (r'^outline-' + _COLOR_WORDS + r'(?:/.*)?$', 'outline-color'), (r'^outline(?:-\d+|-\[.*\])?$', 'outline-w'),
    (r'^ring-offset-', 'ring-offset'), (r'^ring-' + _COLOR_WORDS + r'(?:/.*)?$', 'ring-color'), (r'^ring(?:-\d+|-\[.*\])?$', 'ring-w'), (r'^(?:ring-inset)$', 'ring-inset'),
    (r'^shadow-' + _COLOR_WORDS + r'(?:/.*)?$', 'shadow-color'), (r'^shadow(?:-|$)', 'shadow'),
    (r'^opacity-', 'opacity'), (r'^mix-blend-', 'mix-blend'), (r'^blur(?:-|$)', 'blur'), (r'^backdrop-blur', 'backdrop-blur'),
    (r'^transition(?:-|$)', 'transition'), (r'^duration-', 'duration'), (r'^ease-', 'ease'), (r'^delay-', 'delay'), (r'^animate-', 'animate'),
    (r'^-?translate-x-', 'translate-x'), (r'^-?translate-y-', 'translate-y'), (r'^-?translate-', 'translate'), (r'^-?scale-x-', 'scale-x'), (r'^-?scale-y-', 'scale-y'), (r'^-?scale-', 'scale'), (r'^-?rotate-', 'rotate'), (r'^origin-', 'origin'), (r'^transform', 'transform'),
    (r'^overflow-x-', 'overflow-x'), (r'^overflow-y-', 'overflow-y'), (r'^overflow-', 'overflow'), (r'^overscroll-', 'overscroll'),
    (r'^scroll-(?:auto|smooth)$', 'scroll-behavior'), (r'^-?scroll-m[xytrblse]?-', None), (r'^scroll-p[xytrblse]?-', None),
    (r'^cursor-', 'cursor'), (r'^select-', 'select'), (r'^pointer-events-', 'pointer-events'), (r'^resize', 'resize'), (r'^touch-', 'touch'), (r'^appearance-', 'appearance'), (r'^will-change-', 'will-change'),
    (r'^aspect-', 'aspect'), (r'^object-(?:contain|cover|fill|none|scale-down)$', 'object-fit'), (r'^object-', 'object-position'),
    (r'^fill-', 'fill'), (r'^stroke-\d', 'stroke-w'), (r'^stroke-', 'stroke'), (r'^accent-', 'accent'), (r'^caret-', 'caret'),
    (r'^columns-', 'columns'), (r'^isolate', 'isolate'), (r'^(?:sr-only|not-sr-only)$', 'sr'), (r'^antialiased|subpixel-antialiased$', 'smoothing'),
]
_COMPILED = [(re.compile(p), g) for p, g in GROUPS]

# Groups that clear other groups when set.
CONFLICTS = {
    'p': ['px', 'py', 'pt', 'pr', 'pb', 'pl', 'ps', 'pe'], 'px': ['pr', 'pl', 'ps', 'pe'], 'py': ['pt', 'pb'],
    'm': ['mx', 'my', 'mt', 'mr', 'mb', 'ml', 'ms', 'me'], 'mx': ['mr', 'ml', 'ms', 'me'], 'my': ['mt', 'mb'],
    'gap': ['gap-x', 'gap-y'], 'size': ['w', 'h'], 'inset': ['inset-x', 'inset-y', 'top', 'right', 'bottom', 'left', 'start', 'end'],
    'inset-x': ['left', 'right', 'start', 'end'], 'inset-y': ['top', 'bottom'], 'rounded': [], 'border-w': [], 'translate': ['translate-x', 'translate-y'], 'scale': ['scale-x', 'scale-y'],
    'overflow': ['overflow-x', 'overflow-y'], 'font-size': [], 'truncate': ['overflow', 'text-wrap', 'whitespace'],
    # the `cn` package (shadcn's merger) knows flex-* shorthands set grow/shrink/basis
    'flex': ['grow', 'shrink', 'basis'],
}


def split_variant(cls):
    """Split 'md:hover:bg-x' into ('md:hover:', 'bg-x'), honouring brackets in arbitrary variants."""
    depth = 0
    last = -1
    i = 0
    while i < len(cls):
        c = cls[i]
        if c in '[(':
            depth += 1
        elif c in '])':
            depth -= 1
        elif c == ':' and depth == 0:
            last = i
        i += 1
    if last < 0:
        return '', cls
    return cls[:last + 1], cls[last + 1:]


def group_of(base):
    important = base.startswith('!')
    if important:
        base = base[1:]
    for rx, g in _COMPILED:
        if rx.search(base):
            return g if g is not None else base  # None: keep the exact class as its own group
    return None


def merge(classes):
    """Merge a sequence of class strings/lists the way tailwind-merge does; later classes win."""
    tokens = []
    for c in classes:
        if not c:
            continue
        tokens.extend(c.split() if isinstance(c, str) else c)
    kept = []  # list of (key, class)
    for cls in tokens:
        variant, base = split_variant(cls)
        important = base.startswith('!') or base.endswith('!')
        base = base.strip('!')
        if important:
            variant = '!' + variant
        group = group_of(base)
        if group is None:
            # unknown utility (arbitrary like [--foo:1]); only drop exact duplicates
            kept = [(k, v) for k, v in kept if v != cls]
            kept.append(((variant, cls), cls))
            continue
        key = (variant, group)
        clear = {key} | {(variant, g) for g in CONFLICTS.get(group, [])}
        kept = [(k, v) for k, v in kept if k not in clear]
        kept.append((key, cls))
    return ' '.join(v for _, v in kept)
