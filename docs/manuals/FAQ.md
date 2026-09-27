# Cirqen: frequently asked questions

**I forgot my password.** Ask your HOD to reset it. You will get a one-time
password and choose a new one when you sign in.

**I lost the phone with my authenticator app.** Sign in with one of the
recovery codes you were given when you set it up, then set two-factor up
again on the new phone. With no recovery codes left, ask IT to run
`python manage.py reset_two_factor <username>` (see OPERATIONS.md); you can
then sign in with your password and set it up again.

**Too many failed sign-ins: it says to wait.** After repeated wrong passwords
Cirqen blocks sign-in for a short time to stop guessing. Five wrong passwords lock
the account for 15 minutes; wait, or ask your HOD to reset your password.

**I was signed out while working.** Cirqen signs you out after 30 minutes
without typing or clicking, in case the computer was left unattended. Save
long forms as you go.

**The internet is down. Can I keep working?** Yes. Everything is saved on the
site and sent to HQ later. Only calibration certificate numbers wait for HQ;
the certificate shows "pending number" until then.

**A certificate says its number is pending.** It has been approved but the
site has not reached HQ yet. It gets its number automatically when the link
returns; download it again then.

**Why can't I approve my own calibration?** ISO/IEC 17025 needs results to be
checked by someone other than the person who produced them. Ask another
reviewer or the HOD.

**Cirqen won't let me save a calibration.** Usually: too few readings at a
set value (the message names the rows), or a reference standard past its due
date (renew it, or choose another standard).

**Is a certificate genuine?** Scan its QR code. The HQ page shows the
certificate number, device and date as recorded. If they differ from the
paper, or the page says it is not found, report it to the calibration centre.

**Can I change a certificate after it is issued?** No. An issued certificate
is kept exactly as issued. If it was wrong, the calibration is redone and a
new certificate issued.

**Why can't I see another department's equipment?** Each person sees their
own workshop or department only. The HOD sees everything.

**Someone left the hospital. Delete their account?** Deactivate it instead.
Their name must stay on the records they signed.

**I don't get SMS or email reminders.** Check your phone number and email in
your profile with your HOD. Your site must also have SMS and email set up
(IT).

**Who do I call for help?** Your HOD or site IT first. They contact Cirqen
Labs support (see `SUPPORT.md` for hours and how to report a problem).
