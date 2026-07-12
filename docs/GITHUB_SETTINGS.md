# GitHub settings after merge

After this consolidation is reviewed and merged, configure `main` to:

- require a pull request before merge;
- require `Python 3.11`, `Python 3.12`, `Python quality and coverage`, `Shell`,
  `JavaScript`, `Synthetic Playwright`, and `Dependency and boundary audit`;
- block force pushes and branch deletion;
- keep administrator recovery available;
- avoid mandatory approval counts that lock out a single maintainer;
- leave nightly stress informative unless its stability is established.

Do not configure this from the consolidation PR. Repository visibility,
permissions, secrets, license and external integrations remain unchanged.
