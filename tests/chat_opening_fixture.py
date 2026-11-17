"""What the chat's opening screen needs from the server, and how a test reaches its options.

The opening screen asks which applications the organisation has recorded relationships for and offers a
question about each ("What breaks if <application> fails?"). The loopback servers in the chat tests answer
that request with a seeded organisation's linked applications, so the questions name real records instead
of an empty portfolio. The options sheet is where the assistant, scope and model
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
FIRST_QUESTIONS_PATH = '/ai-chat/first-questions'
# What the first-questions route returns for that organisation: the applications it has recorded
# relationships for, most connected first, each with its own figure.
FIRST_QUESTIONS = {
    'applications': len(APPLICATIONS),
    'questions': [
        {'element_id': 500 + a['id'], 'name': a['name'], 'question': f"What breaks if {a['name']} fails?",
         'detail': f'{5 - n} relationships recorded'}
        for n, a in enumerate(APPLICATIONS[:4])
    ],
}


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
    # Closing hands focus back to the control that opened the sheet, a moment after the sheet is
    # hidden. A person starts typing only once that has happened, so wait for focus to stop moving.
    page.evaluate(
        '''() => new Promise((resolve) => {
            let timer = setTimeout(done, 400);
            function done() { document.removeEventListener('focusin', moved, true); resolve(); }
            function moved() { clearTimeout(timer); timer = setTimeout(done, 400); }
            document.addEventListener('focusin', moved, true);
        })'''
    )
