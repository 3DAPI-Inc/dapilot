# Security

## How credentials are handled

dapilot connects to accounts that spend money and publish in your name: your
3DAPI key, your Etsy shop, fal.ai, and the social accounts you post to.

- Every value is entered on the Integrations page, a local server bound to
  127.0.0.1 that only your browser can reach. The page writes the values to
  an env file in your store directory, mode 0600, and verifies each one with
  a free request to its platform.
- Nothing you type on the page passes through the model. The plugin ships a
  hook that refuses the model's own tools on the env file, the Etsy token and
  the social session files. The hook fences the model's tools, not code it
  might write, so the real protection is that every value is revocable on its
  platform.
- The plugin's scripts read the env file themselves and never print a value.
- Nothing in this repository carries a credential. The templates in
  `plugins/dapilot/templates/` are empty.

## Reporting a vulnerability

Email `dev@dapi.digital` with the details. Do not open a public issue for a
problem that involves keys, tokens or accounts. You will get an answer within
five working days.
