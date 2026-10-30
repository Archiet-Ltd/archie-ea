/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    './app/templates/**/*.html',
    // Blueprint-local template folders (app/modules/<domain>/templates/...).
    // Without this glob Tailwind never sees those files, so a utility used
    // ONLY in a module template is silently absent from tailwind-output.css
    // and the class does nothing at runtime. Found via /modules, whose
    // `focus:ring-2 focus:ring-ring` and `-translate-y-1/2` were unbuilt.
    './app/modules/**/templates/**/*.html',
    './app/static/js/**/*.js',
    // The class lists of the vendored component macros (the Overview dashboard).
    './app/basecoat_kit/data/recipes.json',
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      // The strategy canvases (components/canvas) lay boxes on a ten-column grid
      // and the value proposition canvas on a twenty-by-twenty one, and keep their
      // natural width so a phone scrolls sideways: the spans, starts and widths
      // Tailwind 3 stops short of.
      gridTemplateColumns: { '20': 'repeat(20, minmax(0, 1fr))' },
      gridTemplateRows: { '20': 'repeat(20, minmax(0, 1fr))' },
      gridColumnStart: { '14': '14' },
      gridRowStart: { '12': '12' },
      gridRow: {
        'span-7': 'span 7 / span 7',
        'span-8': 'span 8 / span 8',
        'span-10': 'span 10 / span 10',
        'span-18': 'span 18 / span 18',
      },
      minWidth: { '200': '50rem', '240': '60rem', '260': '65rem', '270': '67.5rem' },
      // Pilot type scale (design register: typography). Plain sizes: the line
      // height stays inherited, as it was with the arbitrary text-[Npx] values.
      // The 11px tier is .text-2xs in shadcn_tokens.css (16px line height).
      fontSize: {
        '10': '10px',
        '13': '13px',
        '15': '15px',
        '28': '28px',
      },
      colors: {
        heat: {
          1: "hsl(var(--heat-1) / <alpha-value>)",
          2: "hsl(var(--heat-2) / <alpha-value>)",
          3: "hsl(var(--heat-3) / <alpha-value>)",
          4: "hsl(var(--heat-4) / <alpha-value>)",
          5: "hsl(var(--heat-5) / <alpha-value>)",
          foreground: "hsl(var(--heat-foreground) / <alpha-value>)",
        },
        chart: {
          1: "hsl(var(--chart-1) / <alpha-value>)",
          2: "hsl(var(--chart-2) / <alpha-value>)",
          3: "hsl(var(--chart-3) / <alpha-value>)",
          4: "hsl(var(--chart-4) / <alpha-value>)",
          5: "hsl(var(--chart-5) / <alpha-value>)",
          tint: "hsl(var(--chart-tint) / <alpha-value>)",
        },
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        overlay: "hsl(var(--overlay) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
          surface: "hsl(var(--primary-surface) / <alpha-value>)",
          "surface-foreground": "hsl(var(--primary-surface-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--destructive-emphasis) / <alpha-value>)",
        },
        success: {
          DEFAULT: "hsl(var(--success) / <alpha-value>)",
          foreground: "hsl(var(--success-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--success-emphasis) / <alpha-value>)",
        },
        warning: {
          DEFAULT: "hsl(var(--warning) / <alpha-value>)",
          foreground: "hsl(var(--warning-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--warning-emphasis) / <alpha-value>)",
        },
        info: {
          DEFAULT: "hsl(var(--info) / <alpha-value>)",
          foreground: "hsl(var(--info-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--info-emphasis) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT: "hsl(var(--popover) / <alpha-value>)",
          foreground: "hsl(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },
        // ArchiMate notation: the fill and hairline stroke each layer is drawn in. Defined once in
        // shadcn_tokens.css (light and dark); the composer's palette, canvas and legend use these classes.
        notation: {
          strategy: {
            DEFAULT: "hsl(var(--notation-strategy) / <alpha-value>)",
            stroke: "hsl(var(--notation-strategy-stroke) / <alpha-value>)",
          },
          business: {
            DEFAULT: "hsl(var(--notation-business) / <alpha-value>)",
            stroke: "hsl(var(--notation-business-stroke) / <alpha-value>)",
          },
          application: {
            DEFAULT: "hsl(var(--notation-application) / <alpha-value>)",
            stroke: "hsl(var(--notation-application-stroke) / <alpha-value>)",
          },
          technology: {
            DEFAULT: "hsl(var(--notation-technology) / <alpha-value>)",
            stroke: "hsl(var(--notation-technology-stroke) / <alpha-value>)",
          },
          physical: {
            DEFAULT: "hsl(var(--notation-physical) / <alpha-value>)",
            stroke: "hsl(var(--notation-physical-stroke) / <alpha-value>)",
          },
          motivation: {
            DEFAULT: "hsl(var(--notation-motivation) / <alpha-value>)",
            stroke: "hsl(var(--notation-motivation-stroke) / <alpha-value>)",
          },
          implementation: {
            DEFAULT: "hsl(var(--notation-implementation) / <alpha-value>)",
            stroke: "hsl(var(--notation-implementation-stroke) / <alpha-value>)",
          },
          migration: {
            DEFAULT: "hsl(var(--notation-migration) / <alpha-value>)",
            stroke: "hsl(var(--notation-migration-stroke) / <alpha-value>)",
          },
          location: {
            DEFAULT: "hsl(var(--notation-location) / <alpha-value>)",
            stroke: "hsl(var(--notation-location-stroke) / <alpha-value>)",
          },
          other: {
            DEFAULT: "hsl(var(--notation-other) / <alpha-value>)",
            stroke: "hsl(var(--notation-other-stroke) / <alpha-value>)",
          },
        },
        // ArchiMate layer hues. The DEFAULT is the fill/border colour; the
        // `-emphasis` twin is the only one legible as text on a /10 tint.
        // See DESIGN.md "ArchiMate Layer Colors".
        layer: {
          motivation: {
            DEFAULT: "hsl(var(--layer-motivation) / <alpha-value>)",
            emphasis: "hsl(var(--layer-motivation-emphasis) / <alpha-value>)",
          },
          strategy: {
            DEFAULT: "hsl(var(--layer-strategy) / <alpha-value>)",
            emphasis: "hsl(var(--layer-strategy-emphasis) / <alpha-value>)",
          },
          business: {
            DEFAULT: "hsl(var(--layer-business) / <alpha-value>)",
            emphasis: "hsl(var(--layer-business-emphasis) / <alpha-value>)",
          },
          application: {
            DEFAULT: "hsl(var(--layer-application) / <alpha-value>)",
            emphasis: "hsl(var(--layer-application-emphasis) / <alpha-value>)",
          },
          technology: {
            DEFAULT: "hsl(var(--layer-technology) / <alpha-value>)",
            emphasis: "hsl(var(--layer-technology-emphasis) / <alpha-value>)",
          },
          implementation: {
            DEFAULT: "hsl(var(--layer-implementation) / <alpha-value>)",
            emphasis: "hsl(var(--layer-implementation-emphasis) / <alpha-value>)",
          },
          risk: {
            DEFAULT: "hsl(var(--layer-risk) / <alpha-value>)",
            emphasis: "hsl(var(--layer-risk-emphasis) / <alpha-value>)",
          },
        },
      },
      // Text role of the status tokens. --destructive, --success and --warning are FILL colours (a dark red,
      // a mid green, an amber): as text they score 1.4 to 3.3 against the page in one theme or the other.
      // Their -emphasis twin is the text colour, tuned per theme, so `text-destructive`, `text-success` and
      // `text-warning` resolve to it. bg-*, border-* and ring-* keep the fill value.
      textColor: {
        destructive: {
          DEFAULT: "hsl(var(--destructive-emphasis) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--destructive-emphasis) / <alpha-value>)",
        },
        success: {
          DEFAULT: "hsl(var(--success-emphasis) / <alpha-value>)",
          foreground: "hsl(var(--success-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--success-emphasis) / <alpha-value>)",
        },
        warning: {
          DEFAULT: "hsl(var(--warning-emphasis) / <alpha-value>)",
          foreground: "hsl(var(--warning-foreground) / <alpha-value>)",
          emphasis: "hsl(var(--warning-emphasis) / <alpha-value>)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // Drive @tailwindcss/typography from the design tokens instead of its
      // stock greys. Every source token is already .dark-aware, so the invert
      // twins take identical values and dark mode resolves per theme without a
      // second `prose-invert` ruleset.
      typography: {
        DEFAULT: {
          css: {
            '--tw-prose-body': 'hsl(var(--foreground))',
            '--tw-prose-headings': 'hsl(var(--foreground))',
            '--tw-prose-lead': 'hsl(var(--muted-foreground))',
            // --info, NOT --primary: --primary is near-white in dark mode
            // (210 40% 98%), which would make links indistinguishable from
            // body text. See DESIGN.md "Note on blue".
            '--tw-prose-links': 'hsl(var(--info))',
            '--tw-prose-bold': 'hsl(var(--foreground))',
            '--tw-prose-counters': 'hsl(var(--muted-foreground))',
            '--tw-prose-bullets': 'hsl(var(--muted-foreground))',
            '--tw-prose-hr': 'hsl(var(--border))',
            '--tw-prose-quotes': 'hsl(var(--muted-foreground))',
            '--tw-prose-quote-borders': 'hsl(var(--border))',
            '--tw-prose-captions': 'hsl(var(--muted-foreground))',
            '--tw-prose-code': 'hsl(var(--foreground))',
            // pre-bg and pre-code must move together, or code blocks render
            // white-on-white in light mode.
            '--tw-prose-pre-code': 'hsl(var(--foreground))',
            '--tw-prose-pre-bg': 'hsl(var(--muted))',
            '--tw-prose-th-borders': 'hsl(var(--border))',
            '--tw-prose-td-borders': 'hsl(var(--border))',
            '--tw-prose-kbd': 'hsl(var(--foreground))',
            // R G B triplet, not HSL: the plugin injects this into
            // rgb(var(--tw-prose-kbd-shadows) / <alpha>).
            '--tw-prose-kbd-shadows': '15 23 42',
            '--tw-prose-invert-body': 'hsl(var(--foreground))',
            '--tw-prose-invert-headings': 'hsl(var(--foreground))',
            '--tw-prose-invert-lead': 'hsl(var(--muted-foreground))',
            '--tw-prose-invert-links': 'hsl(var(--info))',
            '--tw-prose-invert-bold': 'hsl(var(--foreground))',
            '--tw-prose-invert-counters': 'hsl(var(--muted-foreground))',
            '--tw-prose-invert-bullets': 'hsl(var(--muted-foreground))',
            '--tw-prose-invert-hr': 'hsl(var(--border))',
            '--tw-prose-invert-quotes': 'hsl(var(--muted-foreground))',
            '--tw-prose-invert-quote-borders': 'hsl(var(--border))',
            '--tw-prose-invert-captions': 'hsl(var(--muted-foreground))',
            '--tw-prose-invert-code': 'hsl(var(--foreground))',
            '--tw-prose-invert-pre-code': 'hsl(var(--foreground))',
            '--tw-prose-invert-pre-bg': 'hsl(var(--muted))',
            '--tw-prose-invert-th-borders': 'hsl(var(--border))',
            '--tw-prose-invert-td-borders': 'hsl(var(--border))',
            '--tw-prose-invert-kbd': 'hsl(var(--foreground))',
            '--tw-prose-invert-kbd-shadows': '226 232 240',
          },
        },
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  // Bundled in the standalone CLI at scripts/bin/tailwindcss[.exe] — resolves
  // with no npm install and no network, so the air-gap posture is unaffected.
  plugins: [require('@tailwindcss/typography'), require('@tailwindcss/container-queries')],
}
