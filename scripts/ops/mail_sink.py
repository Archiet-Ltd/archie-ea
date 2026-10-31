"""A local mail sink that needs nothing beyond the standard library.

    python scripts/ops/mail_sink.py <port> [bind-address]

Accepts every message, appends it to standard output and answers like a plain SMTP server. The default
address is loopback, so nothing leaves the machine.
"""
import socketserver
import sys


class _Session(socketserver.StreamRequestHandler):
    def _say(self, line):
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def handle(self):
        self._say("220 localhost preview mail sink")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            command = raw.decode("utf-8", "replace").strip()
            verb = command.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._say("250 localhost")
            elif verb == "DATA":
                self._say("354 end data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    row = self.rfile.readline()
                    if not row or row in (b".\r\n", b".\n"):
                        break
                    lines.append(row.decode("utf-8", "replace").rstrip("\r\n"))
                print("---------- MESSAGE FOLLOWS ----------", flush=True)
                print("\n".join(lines), flush=True)
                print("------------ END MESSAGE ------------", flush=True)
                self._say("250 message accepted")
            elif verb == "QUIT":
                self._say("221 bye")
                return
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                self._say("250 ok")
            else:
                self._say("502 command not implemented")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv):
    port = int(argv[1])
    address = argv[2] if len(argv) > 2 else "127.0.0.1"
    with _Server((address, port), _Session) as server:
        server.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
