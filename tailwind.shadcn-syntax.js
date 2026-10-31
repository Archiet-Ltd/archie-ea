// The class syntax of the shadcn components the macros copy (written for Tailwind 4) that
// Tailwind 3, which builds this product's stylesheet, does not read: `data-disabled:`,
// `aria-invalid:`, `has-aria-expanded:`, `nth-last-2:`, `has-data-[slot=card-action]:`,
// `group-has-data-horizontal/field:`, `outline-hidden`, `field-sizing-content`, and a
// custom property in parentheses (`max-h-(--radix-dropdown-menu-content-available-height)`).
// Without a rule for each, a card header with an action is not a two-column grid, an
// invalid field has no red ring and a disabled menu item looks enabled. Each is defined
// here as Tailwind 4 defines it; the names are not listed one by one.
const fs = require('fs')
const path = require('path')
const plugin = require('tailwindcss/plugin')

const ROOTS = ['app/templates', 'app/modules', 'app/static/js', 'app/static/vendor/basecoat', 'app/basecoat_kit/data']
const FILES = /\.(html|js|json)$/
const TAG = /(?<![\w&-])\[([a-z][a-z0-9]*)\]:/g
const PAREN = /(?<![\w-])((?:min-|max-)?[hw]|origin|rounded(?:-[lr])?)-(\(--[\w-]+\))/g

// What the templates, scripts and recipes write in the Tailwind 4 forms above: every
// `prefix-(--name)`, and every `[tag]:` variant (the element is that tag).
function scan() {
  const found = {}
  const tags = new Set()
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (FILES.test(entry.name)) {
        const text = fs.readFileSync(full, 'utf8')
        for (const m of text.matchAll(TAG)) tags.add(m[1])
        for (const m of text.matchAll(PAREN)) {
          const value = m[2]
          found[m[1]] = found[m[1]] || {}
          found[m[1]][value] = `var(${value.slice(1, -1)})`
        }
      }
    }
  }
  for (const root of ROOTS) if (fs.existsSync(root)) walk(root)
  return { found, tags }
}

module.exports = plugin(({ addVariant, matchVariant, addUtilities, e }) => {
  addVariant('aria-invalid', '&[aria-invalid="true"]')
  addVariant('has-aria-expanded', '&:has([aria-expanded="true"])')
  addVariant('nth-last-2', '&:nth-last-child(2)')

  matchVariant('has-data', (value) => `&:has([data-${value}])`)
  matchVariant(
    'group-has-data',
    (value, { modifier }) => `:merge(.group${modifier ? `\\/${modifier}` : ''}):has([data-${value}]) &`,
    { modifiers: 'any', values: { horizontal: 'horizontal', vertical: 'vertical' } }
  )

  addUtilities({
    '.outline-hidden': {
      'outline-style': 'none',
      '@media (forced-colors: active)': { outline: '2px solid transparent', 'outline-offset': '2px' },
    },
    '.field-sizing-content': { 'field-sizing': 'content' },
    '.field-sizing-fixed': { 'field-sizing': 'fixed' },
  })

  // Added as plain utilities named after what the template wrote: registering these names
  // through matchUtilities would shadow Tailwind's own `h`, `w` and `max-h`, and with them
  // every arbitrary value such as `w-[72px]`.
  const properties = {
    h: ['height'], w: ['width'], 'min-w': ['min-width'], 'max-w': ['max-width'],
    'min-h': ['min-height'], 'max-h': ['max-height'], origin: ['transform-origin'],
    rounded: ['border-radius'],
    'rounded-l': ['border-top-left-radius', 'border-bottom-left-radius'],
    'rounded-r': ['border-top-right-radius', 'border-bottom-right-radius'],
  }
  const { found, tags } = scan()
  for (const tag of tags) addVariant(`[${tag}]`, `&:is(${tag})`)
  const rules = {}
  for (const [name, values] of Object.entries(found)) {
    for (const [value, cssValue] of Object.entries(values)) {
      rules[`.${e(`${name}-${value}`)}`] = Object.fromEntries(properties[name].map((p) => [p, cssValue]))
    }
  }
  addUtilities(rules)
})
