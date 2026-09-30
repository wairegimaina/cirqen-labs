"""Create the first admin, or reset one who lost their authenticator.

    python admin_cli.py create-owner <username>
    python admin_cli.py reset-code <username>

Run on the Control service (Render > Shell), where CONTROL_DB points at the
persistent disk. The password is asked for (hidden). The authenticator key is
printed once: add it to an authenticator app as a setup key.
"""
import getpass
import sys

import admin_auth as auth
import control_store as cs


def main(argv):
    if len(argv) != 2 or argv[0] not in ("create-owner", "reset-code"):
        sys.exit(__doc__)
    action, username = argv
    cs.init()
    if action == "create-owner":
        password = getpass.getpass("Password (12+ characters): ")
        if password != getpass.getpass("Again: "):
            sys.exit("Passwords differ.")
        try:
            secret = auth.create_admin(username, password, "owner")
        except auth.AuthError as exc:
            sys.exit(str(exc))
        cs.audit("server shell", "admin_created", username.strip().lower(), {"role": "owner"})
    else:
        admin = auth.get_admin(username=username)
        if admin is None:
            sys.exit(f"No admin {username}.")
        secret = auth.new_totp_secret()
        cs.conn().execute("UPDATE admins SET totp_secret = ?, totp_last_step = 0 WHERE id = ?", (secret, admin["id"]))
        auth.end_all_sessions(admin["id"])
        cs.audit("server shell", "admin_code_reset", admin["username"])
    print("Authenticator setup key (shown once):", secret)
    print(auth.otpauth_uri(username.strip().lower(), secret))


if __name__ == "__main__":
    main(sys.argv[1:])
