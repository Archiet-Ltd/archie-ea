"""
Enterprise Genome — DATA slice deterministic emitter: GDPR Article 30 RoPA.

Renders a :func:`build_data_genome_slice` dict into a **GDPR Article 30 Record of
Processing Activities** table (an HTML ``<table>`` fragment) with a fixed Jinja2
template. Zero LLM, no clock, no randomness — the same slice renders byte-for-byte
identical output every time.

Each row is provenance-linked to its source element: the ``data-element-id`` /
``data-element-type`` attributes carry the ``archimate_element_id`` the row was
derived from, so an auditor (or the test suite) can walk every RoPA row back to a
real modelled element. A row that could not be anchored never reaches this
emitter — the builder raises first.

Honest sparseness (CLAUDE.md "never invent data"): purpose, lawful basis and
retention are shown as an em dash (``—``) when the source model does not record
them. A blank is the truth; a plausible-looking default would be a fabrication.
"""
from __future__ import annotations

from markupsafe import Markup

# Standalone Jinja environment — deliberately independent of the app's Jinja
# globals so the emitter is pure and deterministic (no request/session state).
from jinja2 import Environment

_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

EM_DASH = "—"

# Column order is the Article 30(1) skeleton: processing activity, categories of
# data, accessing elements, lawful basis, retention. Access edges can originate
# from business processes as well as applications; preserve their modelled type.
_ROPA_TEMPLATE = _ENV.from_string(
    """
<table class="w-full text-sm border-collapse" data-genome-slice="data"
       data-spec-hash="{{ slice.spec_hash }}">
  <caption class="sr-only">GDPR Article 30 Record of Processing Activities</caption>
  <thead>
    <tr class="text-left border-b border-border">
      <th scope="col" class="py-2 pr-4 font-semibold">Processing activity</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Data categories</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Accessing elements</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Suppliers</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Flows</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Lawful basis</th>
      <th scope="col" class="py-2 pr-4 font-semibold">Retention</th>
      <th scope="col" class="py-2 font-semibold">Source element</th>
    </tr>
  </thead>
  <tbody>
    {% for a in slice.processing_activities %}
    <tr class="border-b border-border align-top"
        data-element-id="{{ a.provenance.archimate_element_id }}"
        data-element-type="{{ a.provenance.archimate_type }}">
      <td class="py-2 pr-4 font-medium">{{ a.name }}</td>
      <td class="py-2 pr-4">{{ a.data_categories | join(", ") if a.data_categories else dash }}</td>
      <td class="py-2 pr-4">
        {% if a.systems %}
          {% for s in a.systems %}<span class="inline-block">{{ s.name }}
            <span class="text-muted-foreground">({{ s.archimate_type | replace('_', ' ') if s.archimate_type else dash }} · {{ s.access_mode }})</span>{% if not loop.last %}; {% endif %}</span>{% endfor %}
        {% else %}{{ dash }}{% endif %}
      </td>
      <td class="py-2 pr-4">
        {% if a.suppliers %}
          {% for sup in a.suppliers %}<span class="inline-block">{{ sup.name }}{% if not loop.last %}; {% endif %}</span>{% endfor %}
        {% else %}{{ dash }}{% endif %}
      </td>
      <td class="py-2 pr-4">
        {% if a.flows %}
          {% for f in a.flows %}<span class="inline-block">{{ f.name }}
            <span class="text-muted-foreground">({{ f.archimate_type | replace('_', ' ') if f.archimate_type else dash }})</span>{% if not loop.last %}; {% endif %}</span>{% endfor %}
        {% else %}{{ dash }}{% endif %}
      </td>
      <td class="py-2 pr-4">{{ a.lawful_basis if a.lawful_basis else dash }}</td>
      <td class="py-2 pr-4">{{ a.retention if a.retention else dash }}</td>
      <td class="py-2 text-muted-foreground">
        {{ a.provenance.archimate_type }} #{{ a.provenance.archimate_element_id }}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="8" class="py-4 text-muted-foreground">
      No information objects modelled for this organization.
    </td></tr>
    {% endfor %}
  </tbody>
</table>
""".strip()
)


def render_ropa_table(slice_dict: dict) -> Markup:
    """Render the DATA slice into a GDPR Article 30 RoPA HTML table fragment.

    Deterministic: identical input -> byte-identical output. Autoescaped, so all
    element names are HTML-safe (CSP-friendly, no inline scripting).
    """
    # The private Jinja environment has autoescape enabled; all slice values pass
    # through it before this trusted fragment is marked safe for the outer template.
    return Markup(  # nosec B704
        _ROPA_TEMPLATE.render(slice=slice_dict, dash=EM_DASH)
    )
