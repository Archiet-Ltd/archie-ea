"""Attribute engine for the shadcn/ui component macros.

Every macro renders one upstream element: its tag, its ``data-slot``, the class recipe extracted from
the pinned shadcn source (``data/recipes.json``) and the state attributes Radix emits for that part
(``data-state``, ``aria-*``, ``role``, ``data-orientation`` ...). Macros stay thin; this module owns the
contract so it can be unit-tested against the golden markup.

Semantic keyword arguments understood by :func:`slot` (all optional):

    variant, size, spacing ...   cva groups of the recipe (and of the Button recipe for button-styled parts)
    as_                          render with another part's recipe (asChild upstream), e.g. as_='sidebar-menu-button';
                                 as_=none renders the bare element without the Button recipe
    tag                          override the element (asChild with a plain element upstream)
    open, active, checked, pressed, selected, expanded, current, disabled, invalid, required, readonly
    orientation, side, align, dir, inset, multiple, placeholder_shown, indeterminate, icon (svg parts)
    type, href, id, name, value, placeholder, tabindex, role, title, style, for_, aria_label ...
    any other ``data_x`` / ``aria_x`` / plain attribute is passed through as ``data-x`` / ``aria-x`` / ``x``.
"""
import json
import pathlib

from markupsafe import Markup, escape

from .icons import ICONS
from .twmerge import merge

_DATA = pathlib.Path(__file__).resolve().parent / 'data' / 'recipes.json'
RECIPES = json.loads(_DATA.read_text(encoding='utf8')) if _DATA.exists() else {}

VOID = {'input', 'img', 'br', 'hr', 'col', 'area', 'base', 'embed', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

PRIMITIVE_TAGS = {
    'Content': 'div', 'Overlay': 'div', 'Item': 'div', 'Separator': 'div', 'Label': 'div', 'Group': 'div',
    'RadioGroup': 'div', 'CheckboxItem': 'div', 'RadioItem': 'div', 'SubTrigger': 'div', 'SubContent': 'div',
    'Close': 'button', 'Trigger': 'button', 'Action': 'button', 'Cancel': 'button', 'Title': 'h2',
    'Description': 'p', 'Viewport': 'div', 'Indicator': 'span', 'ItemIndicator': 'span', 'Value': 'span',
    'Icon': 'span', 'ScrollUpButton': 'div', 'ScrollDownButton': 'div', 'Thumb': 'span', 'Track': 'span',
    'Range': 'span', 'Root': 'div', 'List': 'div', 'Link': 'a', 'Image': 'img', 'Fallback': 'span',
    'Header': 'div', 'Anchor': 'div', 'Arrow': 'svg', 'Handle': 'div', 'Panel': 'div', 'PanelGroup': 'div',
    'Scrollbar': 'div', 'Input': 'input', 'Empty': 'div', 'Shortcut': 'span', 'Portal': None, 'Provider': None,
    'Sub': None, 'Menu': 'div',
}

VIRTUAL = {
    'dialog', 'dialog-portal', 'alert-dialog', 'alert-dialog-portal', 'sheet', 'sheet-portal', 'drawer', 'drawer-portal',
    'popover', 'hover-card', 'hover-card-portal', 'tooltip', 'tooltip-provider', 'dropdown-menu', 'dropdown-menu-portal',
    'dropdown-menu-sub', 'context-menu', 'context-menu-portal', 'context-menu-sub', 'menubar-menu', 'menubar-portal',
    'menubar-sub', 'select', 'combobox', 'toast-portal', 'form', 'form-field', 'command-dialog',
}

TAGS = {
    'button': 'button', 'badge': 'span', 'item': 'div', 'marker': 'div', 'bubble-content': 'div', 'tooltip-trigger': 'button',
    'popover-trigger': 'button', 'dialog-trigger': 'button', 'alert-dialog-trigger': 'button', 'sheet-trigger': 'button',
    'drawer-trigger': 'button', 'hover-card-trigger': 'button', 'dropdown-menu-trigger': 'button', 'menubar-trigger': 'button',
    'context-menu-trigger': 'span', 'select-trigger': 'button', 'combobox-trigger': 'button', 'navigation-menu-trigger': 'button',
    'collapsible-trigger': 'button', 'accordion-trigger': 'button', 'accordion': 'div', 'accordion-item': 'div',
    'accordion-content': 'div', 'collapsible': 'div', 'collapsible-content': 'div', 'tabs': 'div', 'tabs-list': 'div',
    'tabs-trigger': 'button', 'tabs-content': 'div', 'checkbox': 'button', 'checkbox-indicator': 'span', 'switch': 'button',
    'switch-thumb': 'span', 'radio-group': 'div', 'radio-group-item': 'button', 'radio-group-indicator': 'span',
    'toggle': 'button', 'toggle-group': 'div', 'toggle-group-item': 'button', 'slider': 'span', 'slider-track': 'span',
    'slider-range': 'span', 'slider-thumb': 'span', 'progress': 'div', 'progress-indicator': 'div', 'separator': 'div',
    'avatar': 'span', 'avatar-image': 'img', 'avatar-fallback': 'span', 'aspect-ratio': 'div', 'scroll-area': 'div',
    'scroll-area-viewport': 'div', 'scroll-area-scrollbar': 'div', 'scroll-area-thumb': 'div', 'label': 'label',
    'dialog-overlay': 'div', 'dialog-content': 'div', 'dialog-close': 'button', 'dialog-title': 'h2', 'dialog-description': 'p',
    'alert-dialog-overlay': 'div', 'alert-dialog-content': 'div', 'alert-dialog-title': 'h2', 'alert-dialog-description': 'p',
    'alert-dialog-action': 'button', 'alert-dialog-cancel': 'button', 'sheet-overlay': 'div', 'sheet-content': 'div',
    'sheet-close': 'button', 'sheet-title': 'h2', 'sheet-description': 'p', 'drawer-overlay': 'div', 'drawer-content': 'div',
    'drawer-close': 'button', 'drawer-title': 'h2', 'drawer-description': 'p', 'popover-content': 'div', 'popover-anchor': 'div',
    'hover-card-content': 'div', 'tooltip-content': 'div', 'dropdown-menu-content': 'div', 'dropdown-menu-group': 'div',
    'dropdown-menu-item': 'div', 'dropdown-menu-checkbox-item': 'div', 'dropdown-menu-radio-group': 'div',
    'dropdown-menu-radio-item': 'div', 'dropdown-menu-label': 'div', 'dropdown-menu-separator': 'div',
    'dropdown-menu-sub-trigger': 'div', 'dropdown-menu-sub-content': 'div', 'context-menu-content': 'div',
    'context-menu-group': 'div', 'context-menu-item': 'div', 'context-menu-checkbox-item': 'div', 'context-menu-radio-group': 'div',
    'context-menu-radio-item': 'div', 'context-menu-label': 'div', 'context-menu-separator': 'div', 'context-menu-sub-trigger': 'div',
    'context-menu-sub-content': 'div', 'menubar': 'div', 'menubar-content': 'div', 'menubar-group': 'div', 'menubar-item': 'div',
    'menubar-checkbox-item': 'div', 'menubar-radio-group': 'div', 'menubar-radio-item': 'div', 'menubar-label': 'div',
    'menubar-separator': 'div', 'menubar-sub-trigger': 'div', 'menubar-sub-content': 'div', 'select-value': 'span',
    'select-content': 'div', 'select-group': 'div', 'select-item': 'div', 'select-label': 'div', 'select-separator': 'div',
    'select-scroll-up-button': 'div', 'select-scroll-down-button': 'div', 'navigation-menu': 'nav', 'navigation-menu-list': 'ul',
    'navigation-menu-item': 'li', 'navigation-menu-content': 'div', 'navigation-menu-link': 'a', 'navigation-menu-viewport': 'div',
    'navigation-menu-indicator': 'div', 'command': 'div', 'command-input': 'input', 'command-list': 'div', 'command-empty': 'div',
    'command-group': 'div', 'command-separator': 'div', 'command-item': 'div', 'toast': 'div', 'toast-viewport': 'div',
    'toast-content': 'div', 'toast-title': 'div', 'toast-description': 'div', 'toast-action': 'button', 'toast-close': 'button',
    'toast-icon': 'div', 'resizable-panel-group': 'div', 'resizable-panel': 'div', 'resizable-handle': 'div',
    'sidebar-menu-button': 'a', 'sidebar-menu-sub-button': 'a', 'sidebar-menu-action': 'button', 'sidebar-group-label': 'div',
    'sidebar-group-action': 'button', 'sidebar-rail': 'button', 'sidebar-trigger': 'button', 'sidebar-inset': 'main',
    'sidebar-input': 'input', 'sidebar-separator': 'div', 'sidebar-menu': 'ul', 'sidebar-menu-item': 'li', 'sidebar-menu-sub': 'ul',
    'sidebar-menu-sub-item': 'li', 'pagination-link': 'a', 'breadcrumb-link': 'a', 'carousel-previous': 'button',
    'carousel-next': 'button', 'calendar': 'div', 'chart': 'div', 'field-label': 'label', 'field-legend': 'legend',
    'field-set': 'fieldset', 'field-description': 'p', 'field-error': 'div', 'form-item': 'div', 'form-label': 'label',
    'form-control': 'input', 'form-description': 'p', 'form-message': 'p', 'input-otp': 'input', 'native-select': 'select',
    'native-select-option': 'option', 'native-select-optgroup': 'optgroup', 'questionnaire': 'form', 'questionnaire-item': 'fieldset',
    'questionnaire-choice': 'label', 'questionnaire-choice-input': 'input', 'questionnaire-title': 'legend', 'questionnaire-input': 'input',
    'message-scroller-button': 'button', 'attachment-trigger': 'a', 'attachment-action': 'button', 'code-block-content': 'pre',
    'code-block-code': 'code', 'code-block-copy-button': 'button', 'spinner': 'svg', 'kbd': 'kbd', 'kbd-group': 'kbd',
    'table': 'table', 'table-header': 'thead', 'table-body': 'tbody', 'table-footer': 'tfoot', 'table-row': 'tr',
    'table-head': 'th', 'table-cell': 'td', 'table-caption': 'caption', 'table-container': 'div', 'breadcrumb': 'nav',
    'breadcrumb-list': 'ol', 'breadcrumb-item': 'li', 'breadcrumb-page': 'span', 'breadcrumb-separator': 'li',
    'breadcrumb-ellipsis': 'span', 'pagination': 'nav', 'pagination-content': 'ul', 'pagination-item': 'li',
    'pagination-ellipsis': 'span', 'item-group': 'div', 'item-separator': 'div', 'textarea': 'textarea', 'input': 'input',
    'input-group-control': 'input', 'input-group-button': 'button', 'combobox-chip-input': 'input', 'combobox-chip-remove': 'button',
    'combobox-clear': 'button', 'combobox-input': 'input', 'combobox-item': 'div', 'combobox-content': 'div', 'combobox-list': 'div',
    'combobox-empty': 'div', 'combobox-group': 'div', 'combobox-label': 'div', 'combobox-separator': 'div', 'combobox-collection': 'div',
    'combobox-value': 'span', 'combobox-chips': 'div', 'combobox-chip': 'div', 'alert-dialog-media': 'div', 'alert-dialog-header': 'div',
    'alert-dialog-footer': 'div', 'dialog-header': 'div', 'dialog-footer': 'div', 'sheet-header': 'div', 'sheet-footer': 'div',
    'drawer-header': 'div', 'drawer-footer': 'div', 'popover-header': 'div', 'popover-title': 'div', 'popover-description': 'div',
    'direction': 'div', 'accordion-trigger-icon': 'svg', 'native-select-icon': 'svg', 'questionnaire-choice-indicator-check': 'svg',
    'attachment-description': 'span', 'attachment-title': 'span', 'message-scroller-viewport': 'div', 'questionnaire-description': 'p',
}

# Parts rendered through <Button asChild> upstream: they take the Button recipe (variant/size) in addition to their own slot.
AS_BUTTON = {
    'dialog-trigger', 'alert-dialog-trigger', 'sheet-trigger', 'drawer-trigger', 'popover-trigger', 'hover-card-trigger',
    'tooltip-trigger', 'dropdown-menu-trigger', 'collapsible-trigger', 'pagination-link', 'carousel-previous', 'carousel-next',
    'alert-dialog-action', 'alert-dialog-cancel', 'attachment-action', 'code-block-copy-button', 'combobox-trigger',
    'combobox-chip-remove', 'combobox-clear', 'input-group-button', 'sidebar-trigger', 'message-scroller-button',
    'questionnaire-next', 'questionnaire-previous', 'questionnaire-skip', 'questionnaire-submit', 'toast-action', 'toast-close',
    'dialog-close', 'sheet-close', 'drawer-close',
}
BUTTON_DEFAULTS = {
    'carousel-previous': ('outline', 'icon-sm'), 'carousel-next': ('outline', 'icon-sm'), 'sidebar-trigger': ('ghost', 'icon-sm'),
    'code-block-copy-button': ('ghost', 'icon-xs'), 'combobox-chip-remove': ('ghost', 'icon-xs'), 'combobox-clear': ('ghost', 'icon-xs'),
    'combobox-trigger': ('outline', 'default'), 'input-group-button': ('ghost', 'icon-xs'), 'message-scroller-button': ('secondary', 'icon-sm'),
    'alert-dialog-cancel': ('outline', 'default'), 'questionnaire-previous': ('outline', 'default'), 'questionnaire-skip': ('outline', 'default'),
    'dialog-close': ('ghost', 'icon-sm'), 'sheet-close': ('ghost', 'icon-sm'), 'drawer-close': ('ghost', 'icon-sm'),
    'toast-close': ('ghost', 'icon-xs'), 'toast-action': ('outline', 'xs'), 'pagination-link': ('ghost', 'icon'),
    'attachment-action': ('ghost', 'icon-sm'),
}

# Attributes present on a part by default (observed upstream) that a caller may override.
DEFAULT_ATTRS = {
    'card': {'data-size': 'default'}, 'avatar': {'data-size': 'default'}, 'select-trigger': {'data-size': 'default'},
    'switch': {'data-size': 'default'}, 'native-select': {'data-size': 'default'}, 'native-select-wrapper': {'data-size': 'default'},
    'sidebar-menu-button': {'data-size': 'default'}, 'sidebar-menu-sub-button': {'data-size': 'md'},
    'sidebar': {'data-state': 'expanded', 'data-collapsible': '', 'data-variant': 'sidebar', 'data-side': 'left'},
    'sidebar-container': {'data-side': 'left'}, 'input-group-addon': {'data-align': 'inline-start'}, 'bubble': {'data-align': 'start'},
    'message': {'data-align': 'start'}, 'bubble-reactions': {'data-align': 'start', 'data-side': 'bottom'},
    'attachment': {'data-state': 'idle', 'data-size': 'default', 'data-orientation': 'horizontal'},
    'calendar': {'lang': 'en-US', 'data-mode': 'single'},
    'command-item': {'aria-disabled': 'false', 'aria-selected': 'false', 'data-disabled': 'false', 'data-selected': 'false'},
    'questionnaire-item': {'data-status': 'unanswered'},
    'form-control': {'aria-invalid': 'false'},
}

# cva groups whose data-* mirror upstream emits on every instance (measured over every golden page). For other
# slots the attribute appears only when the caller sets the group explicitly.
ALWAYS_DATA = {
    'accordion': ['orientation'], 'accordion-content': ['orientation'], 'accordion-item': ['orientation'], 'accordion-trigger': ['orientation'],
    'alert-dialog-trigger': ['variant', 'size'], 'attachment': ['size', 'orientation'], 'attachment-action': ['variant', 'size'],
    'attachment-media': ['variant'], 'avatar': ['size'], 'badge': ['variant'], 'bubble': ['variant', 'align'], 'bubble-reactions': ['align', 'side'],
    'button': ['variant', 'size'], 'card': ['size'], 'carousel-next': ['variant', 'size'], 'carousel-previous': ['variant', 'size'],
    'code-block': ['variant', 'size'], 'code-block-copy-button': ['variant', 'size'], 'code-block-line': ['variant'],
    'combobox-chip-remove': ['variant', 'size'], 'combobox-clear': ['variant', 'size'], 'combobox-trigger': ['variant', 'size'],
    'drawer-trigger': ['variant', 'size'], 'empty-icon': ['variant'], 'field': ['orientation'], 'field-legend': ['variant'],
    'hover-card-trigger': ['variant', 'size'], 'input-group-addon': ['align'], 'input-group-button': ['variant', 'size'],
    'item': ['variant', 'size'], 'item-media': ['variant'], 'item-separator': ['orientation'], 'marker': ['variant'], 'menubar': ['orientation'],
    'menubar-trigger': ['orientation'], 'message': ['align'], 'message-scroller-button': ['variant', 'size'], 'native-select': ['size'],
    'native-select-wrapper': ['size'], 'navigation-menu': ['orientation'], 'navigation-menu-list': ['orientation'], 'pagination-link': ['variant', 'size'],
    'questionnaire-next': ['variant', 'size'], 'questionnaire-previous': ['variant', 'size'], 'questionnaire-skip': ['variant', 'size'],
    'questionnaire-submit': ['variant', 'size'], 'select-trigger': ['size'], 'separator': ['orientation'], 'sidebar-container': ['side'],
    'sidebar-menu-button': ['size'], 'sidebar-menu-sub-button': ['size'], 'sidebar-separator': ['orientation'], 'sidebar-trigger': ['variant', 'size'],
    'slider': ['orientation'], 'slider-range': ['orientation'], 'slider-thumb': ['orientation'], 'slider-track': ['orientation'], 'switch': ['size'],
    'tabs': ['orientation'], 'tabs-content': ['orientation'], 'tabs-list': ['variant', 'orientation'], 'tabs-trigger': ['orientation'],
    'toggle-group': ['orientation', 'spacing'], 'toggle-group-item': ['variant', 'size', 'spacing'],
}

CONST = {
    'alert': {'role': 'alert'}, 'field-error': {'role': 'alert'}, 'field': {'role': 'group'}, 'button-group': {'role': 'group'},
    'input-group': {'role': 'group'}, 'input-group-addon': {'role': 'group'}, 'carousel': {'role': 'region', 'aria-roledescription': 'carousel'},
    'carousel-item': {'role': 'group', 'aria-roledescription': 'slide'}, 'breadcrumb-ellipsis': {'role': 'presentation', 'aria-hidden': 'true'},
    'breadcrumb-separator': {'role': 'presentation', 'aria-hidden': 'true'}, 'breadcrumb-page': {'role': 'link', 'aria-disabled': 'true', 'aria-current': 'page'},
    'pagination': {'role': 'navigation'}, 'pagination-ellipsis': {'aria-hidden': 'true'}, 'item-group': {'role': 'list'},
    'item-separator': {'data-orientation': 'horizontal', 'role': 'none'}, 'sidebar-separator': {'data-orientation': 'horizontal', 'role': 'none'},
    'menubar': {'role': 'menubar', 'tabindex': '-1', 'data-orientation': 'horizontal'}, 'menubar-trigger': {'type': 'button', 'role': 'menuitem', 'aria-haspopup': 'menu', 'tabindex': '-1', 'data-orientation': 'horizontal'},
    'radio-group': {'role': 'radiogroup', 'aria-required': 'false', 'dir': 'ltr', 'tabindex': '-1'},
    'radio-group-item': {'type': 'button', 'role': 'radio', 'tabindex': '-1'}, 'checkbox': {'type': 'button', 'role': 'checkbox'},
    'switch': {'type': 'button', 'role': 'switch'}, 'toggle': {'type': 'button'}, 'toggle-group-item': {'type': 'button', 'tabindex': '-1'},
    'toggle-group': {'dir': 'ltr', 'tabindex': '-1'}, 'tabs': {'dir': 'ltr'}, 'tabs-list': {'role': 'tablist', 'tabindex': '-1'},
    'tabs-trigger': {'type': 'button', 'role': 'tab', 'tabindex': '-1'}, 'tabs-content': {'role': 'tabpanel', 'tabindex': '0'},
    'accordion-trigger': {'type': 'button'}, 'accordion-content': {'role': 'region'}, 'collapsible-trigger': {'type': 'button'},
    'select-trigger': {'type': 'button', 'role': 'combobox', 'aria-autocomplete': 'none', 'dir': 'ltr'}, 'select-content': {'role': 'listbox', 'tabindex': '-1'},
    'select-item': {'role': 'option', 'tabindex': '-1'}, 'select-scroll-up-button': {'aria-hidden': 'true'}, 'select-scroll-down-button': {'aria-hidden': 'true'},
    'combobox-trigger': {'type': 'button', 'tabindex': '0', 'role': 'combobox', 'aria-haspopup': 'dialog'}, 'combobox-chips': {'role': 'toolbar'},
    'combobox-chip-input': {'autocomplete': 'off', 'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'none', 'role': 'combobox', 'aria-haspopup': 'listbox', 'aria-autocomplete': 'list'},
    'combobox-chip-remove': {'type': 'button', 'tabindex': '-1'}, 'combobox-clear': {'type': 'button', 'tabindex': '-1'},
    'combobox-input': {'autocomplete': 'off', 'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'none', 'role': 'combobox', 'aria-haspopup': 'listbox', 'aria-autocomplete': 'list'},
    'combobox-list': {'role': 'listbox'}, 'combobox-item': {'role': 'option'}, 'combobox-separator': {'role': 'separator'},
    'command': {'tabindex': '-1', 'cmdk-root': ''}, 'command-empty': {'cmdk-empty': '', 'role': 'presentation'}, 'command-group': {'cmdk-group': '', 'role': 'presentation'},
    'command-input': {'cmdk-input': '', 'autocomplete': 'off', 'autocorrect': 'off', 'spellcheck': 'false', 'aria-autocomplete': 'list', 'role': 'combobox', 'aria-expanded': 'true', 'type': 'text'},
    'command-item': {'cmdk-item': '', 'role': 'option'}, 'command-list': {'cmdk-list': '', 'role': 'listbox', 'tabindex': '-1'}, 'command-separator': {'cmdk-separator': '', 'role': 'separator'},
    'dialog-trigger': {'type': 'button', 'aria-haspopup': 'dialog'}, 'alert-dialog-trigger': {'type': 'button', 'aria-haspopup': 'dialog'},
    'sheet-trigger': {'type': 'button', 'aria-haspopup': 'dialog'}, 'drawer-trigger': {'type': 'button', 'aria-haspopup': 'dialog'},
    'popover-trigger': {'type': 'button', 'aria-haspopup': 'dialog'}, 'dropdown-menu-trigger': {'type': 'button', 'aria-haspopup': 'menu'},
    'dialog-content': {'role': 'dialog', 'tabindex': '-1', 'aria-modal': 'true'}, 'alert-dialog-content': {'role': 'alertdialog', 'tabindex': '-1', 'aria-modal': 'true'},
    'sheet-content': {'role': 'dialog', 'tabindex': '-1', 'aria-modal': 'true'}, 'drawer-content': {'role': 'dialog', 'tabindex': '-1', 'aria-modal': 'true'},
    'popover-content': {'role': 'dialog', 'tabindex': '-1'}, 'hover-card-content': {'tabindex': '-1'}, 'tooltip-content': {'role': 'tooltip'},
    'dialog-close': {'type': 'button'}, 'sheet-close': {'type': 'button'}, 'drawer-close': {'type': 'button'}, 'alert-dialog-action': {'type': 'button'}, 'alert-dialog-cancel': {'type': 'button'},
    'dropdown-menu-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'}, 'dropdown-menu-sub-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'},
    'dropdown-menu-item': {'role': 'menuitem', 'tabindex': '-1'}, 'dropdown-menu-checkbox-item': {'role': 'menuitemcheckbox', 'tabindex': '-1'},
    'dropdown-menu-radio-item': {'role': 'menuitemradio', 'tabindex': '-1'}, 'dropdown-menu-radio-group': {'role': 'group'}, 'dropdown-menu-group': {'role': 'group'},
    'dropdown-menu-separator': {'role': 'separator', 'aria-orientation': 'horizontal'}, 'dropdown-menu-sub-trigger': {'role': 'menuitem', 'aria-haspopup': 'menu', 'tabindex': '-1'},
    'context-menu-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'}, 'context-menu-sub-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'},
    'context-menu-item': {'role': 'menuitem', 'tabindex': '-1'}, 'context-menu-checkbox-item': {'role': 'menuitemcheckbox', 'tabindex': '-1'},
    'context-menu-radio-item': {'role': 'menuitemradio', 'tabindex': '-1'}, 'context-menu-radio-group': {'role': 'group'}, 'context-menu-group': {'role': 'group'},
    'context-menu-separator': {'role': 'separator', 'aria-orientation': 'horizontal'}, 'context-menu-sub-trigger': {'role': 'menuitem', 'aria-haspopup': 'menu', 'tabindex': '-1'},
    'menubar-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'}, 'menubar-sub-content': {'role': 'menu', 'tabindex': '-1', 'aria-orientation': 'vertical'},
    'menubar-item': {'role': 'menuitem', 'tabindex': '-1'}, 'menubar-checkbox-item': {'role': 'menuitemcheckbox', 'tabindex': '-1'},
    'menubar-radio-item': {'role': 'menuitemradio', 'tabindex': '-1'}, 'menubar-radio-group': {'role': 'group'}, 'menubar-group': {'role': 'group'},
    'menubar-separator': {'role': 'separator', 'aria-orientation': 'horizontal'}, 'menubar-sub-trigger': {'role': 'menuitem', 'aria-haspopup': 'menu', 'tabindex': '-1'},
    'navigation-menu': {'data-orientation': 'horizontal', 'dir': 'ltr'}, 'navigation-menu-list': {'data-orientation': 'horizontal', 'dir': 'ltr'},
    'progress': {'role': 'progressbar', 'aria-valuemin': '0'}, 'slider-thumb': {'role': 'slider'},
    'scroll-area': {'dir': 'ltr'}, 'scroll-area-viewport': {'data-radix-scroll-area-viewport': ''}, 'separator': {'role': 'none'},
    'resizable-handle': {'role': 'separator', 'tabindex': '0', 'data-separator': 'inactive'}, 'resizable-panel': {'data-panel': 'true'}, 'resizable-panel-group': {'data-group': 'true'},
    'input-otp': {'autocomplete': 'one-time-code', 'spellcheck': 'false', 'data-input-otp': 'true', 'inputmode': 'numeric'}, 'input-otp-separator': {'role': 'separator'},
    'marker-icon': {'aria-hidden': 'true'}, 'trace-icon': {'aria-hidden': 'true'}, 'trace-collapsible-trigger': {'type': 'button'},
    'message-scroller-content': {'role': 'log', 'aria-relevant': 'additions'}, 'message-scroller-viewport': {'role': 'region', 'tabindex': '0'},
    'message-scroller-button': {'type': 'button', 'tabindex': '-1', 'data-direction': 'end'},
    'attachment-trigger': {'target': '_blank', 'rel': 'noopener noreferrer'},
    'code-block-content': {'dir': 'ltr', 'tabindex': '0'}, 'spinner': {'role': 'status'}, 'questionnaire': {'novalidate': ''},
    'questionnaire-choice-indicator': {'aria-hidden': 'true'}, 'questionnaire-choice-shortcut': {'aria-hidden': 'true'},
    'questionnaire-progress': {'role': 'progressbar', 'aria-live': 'polite', 'aria-valuemin': '1'}, 'sidebar-rail': {'tabindex': '-1'},
    'toast-viewport': {'tabindex': '-1'}, 'toast': {'role': 'status', 'aria-live': 'polite'}, 'toast-close': {'type': 'button'}, 'toast-action': {'type': 'button'},
    'avatar-image': {'alt': ''}, 'hover-card-trigger': {}, 'tooltip-trigger': {}, 'accordion-trigger-icon': {'aria-hidden': 'true'},
    'native-select-icon': {'aria-hidden': 'true'}, 'questionnaire-choice-indicator-check': {'aria-hidden': 'true'},
}
for _part in ('content', 'footer', 'group', 'group-content', 'group-label', 'header', 'input', 'menu', 'menu-action', 'menu-badge',
              'menu-button', 'menu-item', 'menu-sub', 'menu-sub-button', 'menu-sub-item', 'rail', 'separator', 'trigger', 'group-action'):
    CONST.setdefault(f'sidebar-{_part}', {})['data-sidebar'] = _part
CONST['sidebar-inner'] = {'data-sidebar': 'sidebar'}

SVG_ICON = {'accordion-trigger-icon': 'chevron-down', 'native-select-icon': 'chevron-down', 'spinner': 'loader-circle',
            'questionnaire-choice-indicator-check': 'check'}
SVG_CONST = {'xmlns': 'http://www.w3.org/2000/svg', 'width': '24', 'height': '24', 'viewBox': '0 0 24 24', 'fill': 'none',
             'stroke': 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}

OPEN_STATE = {
    'open/closed+aria': ('open', 'closed', 'aria-expanded', False),
    'open/closed': ('open', 'closed', None, False),
    'open/closed+hidden': ('open', 'closed', None, True),
    'aria-only': (None, None, 'aria-expanded', False),
}
OPEN_SLOTS = {
    **{s: 'open/closed+aria' for s in ('dialog-trigger', 'alert-dialog-trigger', 'sheet-trigger', 'drawer-trigger', 'popover-trigger',
                                        'dropdown-menu-trigger', 'menubar-trigger', 'select-trigger', 'combobox-trigger', 'navigation-menu-trigger',
                                        'collapsible-trigger', 'accordion-trigger', 'sidebar-group-label', 'trace-collapsible-trigger',
                                        'dropdown-menu-sub-trigger', 'context-menu-sub-trigger', 'menubar-sub-trigger', 'item', 'input-group-button',
                                        'combobox-chip-input', 'combobox-input', 'input-group-control')},
    **{s: 'open/closed' for s in ('tooltip-trigger', 'hover-card-trigger', 'context-menu-trigger', 'accordion-item', 'collapsible', 'trace-collapsible',
                                   'dialog-overlay', 'alert-dialog-overlay', 'sheet-overlay', 'drawer-overlay', 'sidebar-menu-button', 'popover-trigger')},
    **{s: 'open/closed+hidden' for s in ('accordion-content', 'collapsible-content', 'trace-collapsible-content')},
    **{s: 'open/closed' for s in ('dialog-content', 'alert-dialog-content', 'sheet-content', 'drawer-content', 'popover-content', 'hover-card-content',
                                   'tooltip-content', 'dropdown-menu-content', 'dropdown-menu-sub-content', 'context-menu-content', 'context-menu-sub-content',
                                   'menubar-content', 'menubar-sub-content', 'select-content', 'combobox-content', 'navigation-menu-content',
                                   'navigation-menu-viewport', 'toast')},
}
OPEN_SLOTS['popover-trigger'] = 'open/closed+aria'
OPEN_SLOTS['combobox-trigger'] = 'aria-only'
# Parts that are only sometimes a trigger upstream (asChild): state is emitted only when `open` is passed.
OPTIONAL_OPEN = {'item', 'input-group-button', 'combobox-chip-input', 'combobox-input', 'input-group-control', 'sidebar-menu-button', 'sidebar-group-label'}
CHECKED_SLOTS = {'checkbox', 'switch', 'radio-group-item', 'checkbox-indicator', 'switch-thumb', 'radio-group-indicator',
                 'dropdown-menu-checkbox-item', 'dropdown-menu-radio-item', 'context-menu-checkbox-item', 'context-menu-radio-item',
                 'menubar-checkbox-item', 'menubar-radio-item'}
PRESSED_SLOTS = {'toggle', 'toggle-group-item'}
ACTIVE_SLOTS = {'tabs-trigger', 'tabs-content'}
ACTIVE_FLAG_SLOTS = {'sidebar-menu-button', 'sidebar-menu-sub-button', 'pagination-link', 'input-otp-slot', 'message-scroller-button',
                     'dropdown-menu-trigger', 'collapsible-trigger', 'sidebar-menu-action'}
LINK_SLOTS = {'button', 'badge', 'item', 'marker', 'bubble-content', 'sidebar-menu-button', 'sidebar-menu-sub-button', 'pagination-link',
              'breadcrumb-link', 'navigation-menu-link', 'tooltip-trigger', 'dropdown-menu-item', 'attachment-trigger', 'dialog-trigger',
              'popover-trigger', 'hover-card-trigger', 'collapsible-trigger', 'dropdown-menu-trigger'}


def _snake_to_attr(key):
    if key.endswith('_'):
        key = key[:-1]
    return key.replace('_', '-')


def recipe(name, tag=None):
    rec = RECIPES.get(name) or {}
    if tag and rec.get('alts'):
        for alt in rec['alts']:
            el = alt.get('element', '')
            el_tag = el.lower() if el[:1].islower() or el.lower() in ('input', 'textarea', 'label', 'button', 'div', 'span', 'a') else PRIMITIVE_TAGS.get(el.split('.')[-1])
            if el_tag == tag:
                return alt
    return rec


# Parts that upstream renders as a Button with their own extra cva: the element carries data-slot="button",
# Button's default size class, and this part's size class. (InputGroupButton passes data-size but not size.)
COMPOSITES = {'input-group-button': {'outer': 'button', 'marker': 'cn-input-group-button', 'variant_default': 'ghost', 'size_default': 'xs'}}


def tag_for(name, href=None, override=None):
    if override:
        return override
    rec = recipe(name)
    if href and name in LINK_SLOTS:
        return 'a'
    if name in TAGS:
        return TAGS[name]
    el = rec.get('element', 'div')
    if el and el[0].islower():
        return el
    suffix = el.split('.')[-1] if el else 'div'
    return PRIMITIVE_TAGS.get(suffix, 'div') or 'div'


def _cva(rec, kw, defaults=None):
    """Classes and the group values chosen for a cva recipe. An explicit None selects no option (cva semantics)."""
    out, chosen = [], {}
    cva = rec.get('variants') or {}
    if not cva:
        return out, chosen
    out.extend(cva.get('base', '').split())
    for group, options in cva.get('variants', {}).items():
        if group in kw and kw[group] is None:
            chosen[group] = None
            continue
        key = kw.get(group)
        if key is None:
            key = (defaults or {}).get(group, cva.get('defaults', {}).get(group))
        chosen[group] = key
        if key in options:
            out.extend(options[key].split())
    return out, chosen


def as_slot_for(name, kw):
    if 'as_' in kw:
        return kw['as_']
    if 'as' in kw:
        return kw['as']
    return 'button' if name in AS_BUTTON else None


def classes_for(name, kw=None, extra=''):
    """Merged class list (tailwind-merge semantics) for a part, and the cva groups it consumed."""
    kw = dict(kw or {})
    rec = recipe(name, kw.get('tag'))
    parts = []
    own, chosen = _cva(rec, kw)
    base = rec.get('base_classes', '').split()
    as_name = as_slot_for(name, kw)
    if name in COMPOSITES:
        comp = COMPOSITES[name]
        brec = recipe(comp['outer'])
        bclasses, bchosen = _cva(brec, {'variant': kw.get('variant', comp['variant_default'])})
        parts += brec.get('base_classes', '').split() + bclasses
        chosen = {**chosen, 'variant': bchosen.get('variant'), 'size': kw.get('size', comp['size_default'])}
        as_name = None
    if as_name:
        arec = recipe(as_name)
        defaults = {}
        if as_name == 'button':
            dv, ds = BUTTON_DEFAULTS.get(name, ('default', 'default'))
            defaults = {'variant': dv, 'size': ds}
        aclasses, achosen = _cva(arec, kw, defaults)
        parts += arec.get('base_classes', '').split() + aclasses
        chosen = {**achosen, **chosen}
    parts += base + own
    if name == 'toggle-group-item':
        # Upstream: cn("cn-toggle-group-item ...", toggleVariants({variant, size}), className).
        trec = recipe('toggle')
        tclasses, tchosen = _cva(trec, kw, {'variant': 'default', 'size': 'default'})
        parts += tclasses
        chosen = {**tchosen, **chosen}
    merged = merge([' '.join(parts), extra or ''])
    seen = set()
    unique = [c for c in merged.split() if not (c in seen or seen.add(c))]
    return unique, chosen


class Rendered:
    __slots__ = ('tag', 'attributes', 'void', 'virtual', 'inner')

    def __init__(self, tag, attributes, void, virtual=False, inner=''):
        self.tag = tag
        self.attributes = attributes
        self.void = void
        self.virtual = virtual
        self.inner = Markup(inner)

    def __html__(self):
        return self.attributes


def _bool(v):
    return v is True or v == 'true' or v == ''


def slot(name, kw=None, class_='', id=None, attrs=None):
    """Compute tag and attribute string for the upstream part ``name``."""
    kw = dict(kw or {})
    rec = recipe(name)
    a = {}
    consumed = {'as', 'as_', 'tag', 'icon'}
    if name in VIRTUAL:
        if id:
            a['id'] = id
        a['data-fb'] = name
        a['class'] = ' '.join(['contents'] + (class_ or '').split())
        return Rendered('div', _render(a, attrs), False, virtual=True)

    href = kw.get('href')
    tag = tag_for(name, href, kw.get('tag'))
    rec = recipe(name, kw.get('tag'))
    if not rec.get('no_slot'):
        a['data-slot'] = COMPOSITES[name]['outer'] if name in COMPOSITES else name
    cva_kw = {k: v for k, v in kw.items() if isinstance(v, str) or v is None}
    if 'tag' in kw:
        cva_kw['tag'] = kw['tag']
    classes, chosen = classes_for(name, cva_kw, class_)
    silent = set(rec.get('silent_groups', []))
    inner = ''
    if tag == 'svg':
        icon = kw.get('icon', SVG_ICON.get(name, 'square'))
        a.update(SVG_CONST)
        classes = ['lucide', f'lucide-{icon}'] + [c for c in classes if c not in ('lucide', f'lucide-{icon}')]
        inner = ICONS.get(icon, '')
    if classes:
        a['class'] = ' '.join(classes)

    for k, v in DEFAULT_ATTRS.get(name, {}).items():
        a[k] = v
    if name == 'sidebar' and kw.get('collapsible') == 'none':
        for k in ('data-state', 'data-collapsible', 'data-variant', 'data-side'):
            a.pop(k, None)
    always = set(ALWAYS_DATA.get(name, []))
    mirrored = {d[5:] for d in rec.get('data_attrs', []) if d.startswith('data-')}
    as_for_attrs = as_slot_for(name, kw)
    if (name in AS_BUTTON and as_for_attrs == 'button') or name in COMPOSITES:
        always |= {'variant', 'size'}
    if as_for_attrs:
        mirrored |= {d[5:] for d in recipe(as_for_attrs).get('data_attrs', []) if d.startswith('data-')}
    for g, val in chosen.items():
        if val is not None and g not in silent and (g in always or (g in mirrored and kw.get(g) is not None)):
            a[f'data-{g}'] = val
        consumed.add(g)
    for g in ('variant', 'size', 'spacing'):
        if g not in chosen and kw.get(g) is not None:
            a[f'data-{g}'] = kw[g]
            consumed.add(g)
    as_name = as_slot_for(name, kw)
    if as_name and as_name != 'button':
        for k, v in {**DEFAULT_ATTRS.get(as_name, {}), **CONST.get(as_name, {})}.items():
            a.setdefault(k, v)
    for k, v in CONST.get(name, {}).items():
        a[k] = v

    # ---- open / closed
    if name in OPEN_SLOTS and ('open' in kw or name not in OPTIONAL_OPEN):
        o_val, c_val, aria, hide = OPEN_STATE[OPEN_SLOTS[name]]
        is_open = _bool(kw.get('open', False))
        if o_val:
            a['data-state'] = o_val if is_open else c_val
        if aria:
            a[aria] = 'true' if is_open else 'false'
        if hide and not is_open:
            a['hidden'] = ''
    consumed.add('open')

    # ---- checked
    if name in CHECKED_SLOTS:
        indicator = name in ('checkbox-indicator', 'switch-thumb', 'radio-group-indicator')
        checked = kw.get('checked', True if indicator else False)
        if checked == 'indeterminate' or _bool(kw.get('indeterminate', False)):
            a['data-state'] = 'indeterminate'
            if not indicator:
                a['aria-checked'] = 'mixed'
        else:
            a['data-state'] = 'checked' if _bool(checked) else 'unchecked'
            if not indicator:
                a['aria-checked'] = 'true' if _bool(checked) else 'false'
        if name in ('checkbox', 'switch', 'radio-group-item'):
            a['value'] = kw.get('value', 'on')
            consumed.add('value')
    if name in ('combobox-item', 'select-item') and 'checked' in kw:
        on = _bool(kw['checked'])
        a['aria-selected'] = 'true' if on else 'false'
        if name == 'combobox-item':
            a['data-checked'] = 'true' if on else 'false'
        else:
            a['data-state'] = 'checked' if on else 'unchecked'
    consumed |= {'checked', 'indeterminate'}

    # ---- pressed / toggle groups
    if name in PRESSED_SLOTS:
        on = _bool(kw.get('pressed', False))
        a['data-state'] = 'on' if on else 'off'
        if name == 'toggle-group-item' and not _bool(kw.get('multiple', False)):
            a['role'] = 'radio'
            a['aria-checked'] = 'true' if on else 'false'
        else:
            a['aria-pressed'] = 'true' if on else 'false'
    if name == 'toggle-group':
        a['role'] = 'toolbar' if _bool(kw.get('multiple', False)) else 'radiogroup'
    consumed |= {'pressed', 'multiple'}

    # ---- tabs / active flags
    if name in ACTIVE_SLOTS:
        active = _bool(kw.get('active', False))
        a['data-state'] = 'active' if active else 'inactive'
        if name == 'tabs-trigger':
            a['aria-selected'] = 'true' if active else 'false'
        elif not active:
            a['hidden'] = ''
    elif name in ACTIVE_FLAG_SLOTS and 'active' in kw:
        a['data-active'] = 'true' if _bool(kw['active']) else 'false'
        if name == 'pagination-link' and _bool(kw['active']):
            a['aria-current'] = 'page'
    elif as_name in ACTIVE_FLAG_SLOTS and 'active' in kw:
        a['data-active'] = 'true' if _bool(kw['active']) else 'false'
    consumed.add('active')
    if name == 'table-row' and 'selected' in kw and kw['selected'] is not None:
        a['data-state'] = 'selected' if _bool(kw['selected']) else 'false'
    consumed.add('selected')
    if name == 'sidebar' and 'collapsed' in kw:
        a['data-state'] = 'collapsed' if _bool(kw['collapsed']) else 'expanded'
    consumed.add('collapsed')

    # ---- orientation
    orientation = kw.get('orientation')
    if orientation is None and name in ('accordion', 'accordion-item', 'accordion-trigger', 'accordion-content'):
        orientation = 'vertical'
    if orientation is None and name in ('tabs', 'tabs-list', 'tabs-trigger', 'tabs-content', 'separator', 'slider', 'slider-track',
                                        'slider-range', 'slider-thumb', 'toggle-group'):
        orientation = 'horizontal'
    if orientation is None and name == 'field':
        orientation = 'vertical'
    if orientation is not None:
        a['data-orientation'] = orientation
        if name in ('tabs-list', 'slider-thumb', 'resizable-handle'):
            a['aria-orientation'] = orientation
    consumed.add('orientation')

    for key, attr in (('side', 'data-side'), ('align', 'data-align'), ('dir', 'dir')):
        if kw.get(key) is not None:
            a[attr] = kw[key]
        consumed.add(key)
    if _bool(kw.get('inset', False)):
        a['data-inset'] = 'true'
    consumed.add('inset')
    if 'current' in kw and _bool(kw['current']):
        a['aria-current'] = 'page'
    consumed.add('current')

    # ---- disabled / invalid / required / readonly
    if _bool(kw.get('disabled', False)):
        if tag in ('button', 'input', 'select', 'textarea', 'fieldset', 'option', 'optgroup') and name not in ('combobox-chip-remove',):
            a['disabled'] = ''
        if name in ('field', 'input-group', 'command-item'):
            a['data-disabled'] = 'true'
        elif name not in ('input', 'textarea', 'native-select', 'native-select-option', 'native-select-optgroup', 'button', 'field-set',
                          'label', 'form-control', 'sidebar-input', 'carousel-previous', 'carousel-next', 'input-otp', 'input-group-control'):
            a['data-disabled'] = ''
        if name in ('slider', 'combobox-chip', 'combobox-chip-remove', 'dropdown-menu-item', 'context-menu-item', 'menubar-item', 'select-item', 'combobox-item', 'command-item'):
            a['aria-disabled'] = 'true'
        if name == 'slider-thumb':
            a.pop('tabindex', None)
    else:
        if name == 'slider':
            a['aria-disabled'] = 'false'
        if name == 'slider-thumb':
            a['tabindex'] = '0'
    if name == 'slider' and a.get('data-orientation', 'horizontal') != 'vertical' and kw.get('dir') is None:
        a['dir'] = 'ltr'
    if _bool(kw.get('invalid', False)):
        if name == 'field':
            a['data-invalid'] = 'true'
        else:
            a['aria-invalid'] = 'true'
    if _bool(kw.get('required', False)):
        a['required'] = ''
    if _bool(kw.get('readonly', False)):
        a['readonly'] = ''
    consumed |= {'disabled', 'invalid', 'required', 'readonly'}

    # ---- component specifics
    if name == 'select-trigger' and _bool(kw.get('placeholder_shown', True)):
        a['data-placeholder'] = ''
    consumed.add('placeholder_shown')
    if name in ('progress', 'progress-indicator'):
        mx = kw.get('max', 100)
        val = kw.get('value')
        a['data-max'] = str(mx)
        if name == 'progress':
            a['aria-valuemax'] = str(mx)
        if val is None:
            a['data-state'] = 'indeterminate'
        else:
            a['data-state'] = 'complete' if float(val) >= float(mx) else 'loading'
            a['data-value'] = str(val)
            if name == 'progress':
                a['aria-valuenow'] = str(val)
            else:
                a['style'] = f'transform: translateX(-{100 - (float(val) / float(mx)) * 100:g}%)'
        consumed |= {'max', 'value'}
    if name == 'slider-thumb':
        a['aria-valuemin'] = str(kw.get('min', 0))
        a['aria-valuemax'] = str(kw.get('max', 100))
        if kw.get('value') is not None:
            a['aria-valuenow'] = str(kw['value'])
        consumed |= {'min', 'max', 'value'}
    if name == 'questionnaire-progress':
        cur, tot = int(kw.get('current', 1)), int(kw.get('total', 1))
        a.update({'data-current': str(cur), 'data-total': str(tot), 'aria-valuemax': str(tot), 'aria-valuenow': str(cur), 'aria-valuetext': f'Question {cur} of {tot}'})
        if cur == 1:
            a['data-first'] = ''
        if cur == tot:
            a['data-last'] = ''
        consumed |= {'current', 'total'}

    # ---- type / id / href
    if 'type' in kw and kw['type'] is not None:
        a['type'] = kw['type']
    consumed.add('type')
    if id:
        a['id'] = id
    if href:
        a['href'] = href
    consumed.add('href')
    for k, v in kw.items():
        if k in consumed or v is None or v is False:
            continue
        a[_snake_to_attr(k)] = '' if v is True else v
    return Rendered(tag, _render(a, attrs), tag in VOID, inner=inner)


def _render(a, attrs):
    extra = dict(attrs or {})
    for k, v in extra.items():
        if v is None or v is False:
            continue
        if k == 'class' and v:
            a['class'] = merge([a.get('class', ''), str(v)])
        else:
            a[k] = '' if v is True else v
    parts = []
    for k, v in a.items():
        if v == '' or v is True:
            parts.append(f' {k}=""')
        else:
            parts.append(f' {k}="{escape(v)}"')
    return Markup(''.join(parts))
