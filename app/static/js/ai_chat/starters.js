/* The chat's starters: Ask's question chips and element picker.
 *
 * A person chooses a question, types a name, picks a match, and the chat shows
 * that question's answer card. The picker is the one Ask uses (picker.js); this
 * only says what happens when a match is chosen. The answer itself is fetched by
 * app.js (ArchieChat.runStarter), which sends the starter to the server so no
 * model is asked.
 *
 * Registered as a top-level window factory, as the Ask page's component is.
 */
function chatStarters(prefix) {
    var Intelligence = window.Intelligence;
    return Object.assign(Intelligence.picker(prefix), {
        question: 'impact',
        element: null,
        hint: '',

        choose_question: function (key) {
            this.question = key;
            if (this.element) {
                this.run();
                return;
            }
            this.hint = 'Type a name and pick a match to ask this.';
            if (this.$refs.pickerInput) { this.$refs.pickerInput.focus(); }
        },

        onSelect: function (option) {
            this.element = option;
            this.hint = '';
            this.run();
        },

        run: function () {
            if (!this.element || !window.ArchieChat || !window.ArchieChat.runStarter) { return; }
            window.ArchieChat.runStarter(this.question, this.element);
        }
    });
}
window.chatStarters = chatStarters;
