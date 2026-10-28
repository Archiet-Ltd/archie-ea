"""What the chat's opening screen needs from the server, and how a test reaches its options.

The opening screen asks the organisation's records for applications and builds its four questions from
them ("What breaks if <application> fails?"). The loopback servers in the chat tests answer that request
with a seeded organisation's applications in the shape the applications list returns, so the questions
name real records instead of an empty portfolio. The options sheet is where the assistant, scope and model
selectors live; opening and closing it is what a person does, so the tests do the same.
"""
from playwright.sync_api import expect

# A seeded organisation's applications, in the order the list returns them (strategic first).
APPLICATIONS = [
    {'id': 31, 'name': 'Fixture Payments', 'description': 'Takes card payments', 'lifecycle_status': 'operational', 'capability_count': 3},
    {'id': 32, 'name': 'Fixture Ledger', 'description': 'General ledger', 'lifecycle_status': 'operational', 'capability_count': 2},
    {'id': 33, 'name': 'Fixture Billing', 'description': 'Invoices and renewals', 'lifecycle_status': 'development', 'capability_count': 1},
    {'id': 34, 'name': 'Fixture Payroll', 'description': 'Monthly payroll', 'lifecycle_status': 'deprecated', 'capability_count': 1},
    {'id': 35, 'name': 'Fixture Archive', 'description': 'Document archive', 'lifecycle_status': 'retired', 'capability_count': 0},
]
APPLICATIONS_PATH = '/applications/api/list'


def open_chat_options(page):
    """Open the Chat options sheet, where the assistant, scope, model and context controls are."""
    page.get_by_role('button', name='Chat options', exact=True).click(timeout=3000)
    sheet = page.get_by_role('dialog', name='Chat options')
    expect(sheet).to_be_visible()
    return sheet


def close_chat_options(page):
    """Close the sheet with Escape, as a keyboard user does, and check the composer is reachable again."""
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog', name='Chat options')).to_be_hidden()
