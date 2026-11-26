#!/bin/sh
# The "before" state: a teammate made signup_cta last sprint and added its
# variation without a key. Run once, from demo/, after `flagsmith init`.
set -e
flagsmith feature create signup_cta --value "Join the waitlist" --enabled
flagsmith feature variant add signup_cta --value "Get early access" --weight 0
flagsmith feature get signup_cta
