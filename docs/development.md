# freesounder — development, beta distribution, and next steps

Facts below were checked against the named official pages on **10 November 2026, Australia/Melbourne**. Limits and fees change; re-check the source before paying or inviting testers.

**Superseded later the same day:** ingress-only v1 (no Music Assistant, no in-app catalogue) is withdrawn, as is any cupboard server box. v1 is **player-only** *and* has a **full in-app catalogue**: Music Assistant runs in official Docker on an elected **4 GB household-host player**; all rooms are snapclients of that host’s built-in snapserver; the app talks to any player, which proxies to MA (see [architecture.md](architecture.md) §1, §5 and §7).

**Decided 10 November 2026, Australia/Melbourne** (also recorded in architecture.md §10):

- Default household host is a **Pi 4 4GB** player (Pi 5 4GB when lead time is acceptable). One 4 GB host per home; cheaper boards may be members.
- First public beta uses **backup and restore** of Music Assistant’s `/data`. No automatic host failover or live `/data` replication in that beta.
- **Both** Spotify paths are supported and **user-selectable**: Music Assistant **Soloist** and **go-librespot** Connect, with ToS and account-limit disclosures in the app.

## Recommendation

**Stay on Flutter.** Do not switch the client to React Native to get Expo EAS Update. That would throw away the shared Flutter design system already decided for iOS, Android and web.

For betas:

- **iOS:** [TestFlight](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/) once an Apple Developer Program membership is in place.
- **Android:** [Play Console internal testing](https://support.google.com/googleplay/android-developer/answer/9845334) for the team (fast, up to 100 testers). Use a **closed test** later if a *personal* Play developer account created after 13 November 2023 needs production access.
- **Optional, no store:** [Firebase App Distribution](https://firebase.google.com/docs/app-distribution) for both platforms, useful before store accounts exist or for a build that should not go through TestFlight / Play review yet.
- **F-Droid later**, once the Android app is a FLOSS build with no proprietary SDKs (see below). Do not take a Firebase dependency if an F-Droid flavour is a goal.

OTA “push a Dart fix without a store review” is **not built into Flutter**. Shorebird is an optional paid hosted service if we ever want that; it is not required for MVP.

## iOS — TestFlight

TestFlight is included with an [Apple Developer Program](https://developer.apple.com/programs/) membership. Apple’s enrollment help states the annual fee is **99 USD**, “in local currency where available,” and that “prices may vary by region and are listed in local currency during the enrollment process” ([Program enrollment](https://developer.apple.com/help/account/membership/program-enrollment/)). On the **Australian App Store** listing for the Apple Developer app, the in-app purchase “Apple Developer Program” was shown as **A$144.99** when fetched on 10 November 2026, Australia/Melbourne ([apps.apple.com/au/app/apple-developer/id640199958](https://apps.apple.com/au/app/apple-developer/id640199958)). App Store prices typically include GST; the web enrollment price can differ from the in-app subscription price, so treat A$144.99 as the App Store IAP figure, not a guarantee of the web checkout total.

You can install development builds on your own devices with Xcode without enrolling; you need the Program to use TestFlight and to ship on the App Store.

Limits and rules from Apple’s TestFlight help (same date):

| Fact | Official figure | Source |
|---|---|---|
| Internal testers | Up to **100** App Store Connect users who have access to your content | [Add internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers), [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/) |
| External testers | Up to **10,000** per app (people who are not App Store Connect users) | [Invite external testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers/), TestFlight overview |
| Build expiry | Testers can use a build for up to **90 days** from upload; it then becomes unavailable unless you upload a new one | TestFlight overview (“You can test a build for up to 90 days”; “Your build becomes unavailable for testers after 90 days”) |
| Beta App Review | Required for **external** testing. The **first** build of a version submitted to an external group goes to App Review; later builds for the same version “might not” need a full review. You can submit up to **six** builds for TestFlight App Review in a 24-hour period. An external group also requires that an internal group already exists. | Invite external testers; TestFlight overview |
| Public link | External groups can invite testers by email and/or a **public invitation link**. The link can be open to anyone or filtered by device/OS, with an optional cap of **1–10,000** testers on that link. Anyone can reshare the link; testers who join via the public link appear as anonymous in App Store Connect. | Invite external testers |
| Internal-only builds | Builds uploaded as “TestFlight Internal Only” cannot go to external testers or to customers | Invite external testers |

Internal testers are the right first step (no external review). Use a public link only when we actually want a wide, sharable beta.

## Android — Play Console

There is a **one-time US$25 registration fee** for a Play Console developer account (not an annual fee) ([Get started with Play Console](https://support.google.com/googleplay/android-developer/answer/6112435)).

Tracks, from Google’s help pages:

| Track | What it is | Notes | Source |
|---|---|---|---|
| **Internal testing** | Up to **100** chosen testers per app | Builds are “normally available to testers **within seconds**” of being added (testing-requirements page) or “**within minutes**” (set-up page). You can start an internal test **before** finishing app setup. It does **not** count toward the production-access rule below. | [Set up an open, closed, or internal test](https://support.google.com/googleplay/android-developer/answer/9845334); [App testing requirements for new personal developer accounts](https://support.google.com/googleplay/android-developer/answer/14151465) |
| **Closed testing** | A testers list you control (email / Google Groups / opt-in link) | Required before a new personal account can apply for production. | Same pages |
| **Open testing** | Anyone can join from the Play listing | Becomes available **after** you gain production access (for accounts under the 2023+ personal-account rule). | Testing requirements page |

**2023+ rule for new personal accounts.** Personal developer accounts **created after 13 November 2023** must run a **closed test** with a **minimum of 12 testers** who have been **opted in continuously for at least 14 days** before they can apply for production (and for pre-registration). Testers who opt out early do not count; if they opt back in, the 14 days start again. Internal testing does not satisfy this. Open testing is not a substitute. After the criteria are met you apply from the Play Console dashboard and answer questions about the test, the app, and production readiness. Google says review “usually takes seven days or less, but can occasionally take longer.” ([App testing requirements for new personal developer accounts](https://support.google.com/googleplay/android-developer/answer/14151465).)

If the Play account is an **organization** account, or a personal account created on or before 13 November 2023, this particular 12-tester / 14-day gate may not apply — confirm the account type in Play Console rather than assuming. For a brand-new personal account, plan a closed-test cohort well before any public launch.

## Firebase App Distribution (optional, no store)

[Firebase App Distribution](https://firebase.google.com/docs/app-distribution) distributes iOS and Android pre-release builds from one console (or the CLI, fastlane, Gradle, or the REST API), with email invitations and tester groups. Official limits: **500 testers per Firebase project**, **200 testers per group**, with a no-cost increase request possible ([Add and remove testers](https://firebase.google.com/docs/app-distribution/add-remove-testers), last updated 5 November 2026 UTC). The SDK is optional for distribution itself (“no SDK to install” is listed as a capability).

This is useful for early CI artifacts and testers who are not yet on TestFlight or Play internal testing. Caveats: iOS still needs valid signing (ad hoc / development profiles typically require each tester device’s UDID — see [Register additional iOS devices](https://firebase.google.com/docs/app-distribution/register-additional-devices)); Android sideload / Play internal app sharing paths have their own install-friction. Firebase / Play Services / Crashlytics SDKs are **not acceptable in an F-Droid main-repo build** (next section), so keep distribution tooling in CI, not as a required app dependency, if F-Droid remains a goal.

## F-Droid (later)

F-Droid is a good eventual channel for a **free, open-source** Android app, but it is a packaging and policy process, not a quick beta track.

[Inclusion Policy](https://f-droid.org/docs/Inclusion_Policy/): apps in the main repository must be FLOSS; “The implementation of proprietary tracking or advertising libraries and analytics tools such as Google Play Services and Firebase and Crashlytics and proprietary ad/tracking SDKs are **strictly forbidden**.” The [Submitting to F-Droid Quick Start Guide](https://f-droid.org/docs/Submitting_to_F-Droid_Quick_Start_Guide/) repeats that Firebase and GMS are notable non-FOSS libraries that are not accepted, unless you ship a flavour without them. F-Droid builds from source with a FLOSS toolchain.

[Reproducible Builds](https://f-droid.org/docs/Reproducible_Builds/) are **not required**, but F-Droid “strongly encourages” them for new apps (quick-start guide): they let F-Droid verify that its rebuild matches the developer’s APK and can keep the developer’s signing key. Switching to reproducible, developer-signed packages later is painful because Android cannot update an app with a different signing key without a reinstall. If we want F-Droid, design for a clean FOSS product flavour and think about reproducibility **before** the first widely installed Android signing key is frozen.

## CI

Two reasonable paths, both compatible with Flutter:

- **GitHub Actions + [fastlane](https://fastlane.tools/)** in this public GitHub repo: build, sign (using secrets or a cloud signing service), and upload to TestFlight, Play internal testing, and optionally Firebase App Distribution. This keeps CI next to the code and avoids a second vendor for the default pipeline.
- **[Codemagic](https://codemagic.io/)** (or similar Flutter-focused CI) if we want a hosted workflow that already knows how to talk to App Store Connect and Play Console, at the cost of another account and billing relationship.

Stand up Actions first when the developer accounts exist. Do not put provisioning passwords or keystores in the repo.

## OTA code updates

Flutter has **no Expo-style over-the-air update** in the framework. A new Dart UI or bugfix normally ships as a new store (or TestFlight / Play testing) build.

**[Shorebird](https://shorebird.dev/)** is a hosted Code Push service for Flutter (a Flutter-engine add-on, not a replacement of the Dart UI toolkit). Official site, fetched 10 November 2026: “Free for 5,000 patch installs a month”; patches can update Dart code and take effect on the next launch; native code, permissions and assets still need a store build; Shorebird states it has **no self-hosted** offering. Billing is by **successful patch installs** ([Billing](https://docs.shorebird.dev/account/billing/)). The same billing document uses a **Pro** plan example of **US$20 a month**, including **50,000** patch installs. Self-service **Business** and negotiated **Enterprise** plans also exist ([Plans & Upgrading](https://docs.shorebird.dev/account/plans/)); this pass did not record a current official dollar price for Business from a live pricing table, so none is quoted here. Shorebird is a commercial hosted product; evaluate licence, ToS and store-compliance claims if we ever adopt it. It is **optional**, not part of MVP.

**Expo is React Native-only.** [EAS Update](https://docs.expo.dev/eas-update/introduction/) would give a first-party OTA story, but only if we abandoned Flutter. The cost is the already-decided shared Flutter design system, plus rewriting the rooms panel and clients. That trade is not worth it for v1.

## Player daemon language

**Recommend Python** for `services/player-daemon`.

The control API is the product’s control plane: mDNS, household-host election, Music Assistant Docker lifecycle on the host, first-beta `/data` backup/restore (live replication and automatic failover come later), `snapclient` pointing at the host, a facade over the host MA WebSocket API, and setup endpoints. Python is the v1 default because it is fast to iterate, matches the official [`music-assistant-client`](https://github.com/music-assistant/client), and is acceptable on a Pi 4 4GB host (member-only images can stay slim).

TypeScript and Go remain alternatives (Go if we later want a smaller binary on a Zero 2 W member). Expose an OpenAPI (or similar) contract so the Flutter `api_client` package does not depend on Python.

## Local development

Default Compose path, on one machine:

- one **household-host** profile: official `ghcr.io/music-assistant/server` with **host networking** and a `/data` volume, plus the Python daemon and a `snapclient`
- one **member** profile: daemon + `snapclient` pointed at the host (dummy, file, or desktop sink)
- the Flutter app browses mDNS or is pointed at either daemon and exercises **browse/search** against MA through the proxy

Document `cp .env.example .env && docker compose up`. Two real Pis (one 4 GB host, one member) remain the better test. Ordinary regroup should *not* require snapclient retarget; only host failover should.

## Next steps

1. **Monorepo layout scaffold** (Apache-2.0 first-party; do not vendor GPL sources):

   ```
   freesounder/
     LICENSE, NOTICE, README.md
     docs/                     architecture.md, development.md, …
     apps/flutter/             iOS, Android, web client
     packages/design/          tokens, room tile, now-playing, rooms panel
     packages/api_client/      talks only to a player daemon
     services/control-api/     election, MA proxy, /data backup/restore
     services/setup/           first-boot, room naming, household pairing
     images/player/            Pi OS image: daemon + Docker; MA container
                               on 4 GB host profile; snapclient + ALSA on every board
     dev/                      docker-compose: MA host + member snapclients
   ```

2. **Player image with MA + daemon.** Official MA Docker on a 4 GB host profile; snapclient on every board; daemon starts MA only when elected host.

3. **Household host election.** Prefer Ethernet + most RAM; single writer for `/data`; mDNS name for the current host. First beta: documented backup/restore of `/data`. Automatic standby replication and failover come later.

4. **Flutter app on the MA API** (via the daemon facade): rooms-panel shell plus browse/search/queue for the in-app catalogue; discover any player via mDNS; honest outage/Syncing if the host moves.

5. **CI:** GitHub Actions on the Dart app and the Python control-api; add fastlane or Codemagic for TestFlight / Play internal / optional Firebase once accounts exist.

Do not start a from-scratch sync layer, a cupboard MA server, a custom streaming-provider stack, or a React Native rewrite.


## Player image (flashing)

A 64-bit Lite image for Pi 4 and Pi 5 is built from [images/player](../images/player/README.md) (`pi-gen` arm64 branch, Trixie). Flash it with Raspberry Pi Imager: **Use custom**, then **OS customisation** to set a username, password, unique hostname and Wi-Fi. The image has SSH on and **no default password**. See that README for `freesounder-setup`, DAC overlays, host vs room roles, and build instructions. Early images are alpha and were not boot-tested on real hardware in the first build environment.

## Sources

- [Apple Developer Program](https://developer.apple.com/programs/)
- [Apple Program enrollment (fees)](https://developer.apple.com/help/account/membership/program-enrollment/)
- [Australian App Store: Apple Developer app](https://apps.apple.com/au/app/apple-developer/id640199958) (IAP “Apple Developer Program” A$144.99 when fetched 10 November 2026, Australia/Melbourne)
- [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/)
- [Add internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers)
- [Invite external testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers/)
- [Get started with Play Console (US$25 one-time fee)](https://support.google.com/googleplay/android-developer/answer/6112435)
- [Set up an open, closed, or internal test](https://support.google.com/googleplay/android-developer/answer/9845334)
- [App testing requirements for new personal developer accounts](https://support.google.com/googleplay/android-developer/answer/14151465)
- [Firebase App Distribution](https://firebase.google.com/docs/app-distribution)
- [Firebase App Distribution tester limits](https://firebase.google.com/docs/app-distribution/add-remove-testers)
- [F-Droid Inclusion Policy](https://f-droid.org/docs/Inclusion_Policy/)
- [Submitting to F-Droid Quick Start Guide](https://f-droid.org/docs/Submitting_to_F-Droid_Quick_Start_Guide/)
- [F-Droid Reproducible Builds](https://f-droid.org/docs/Reproducible_Builds/)
- [Shorebird](https://shorebird.dev/), [Shorebird billing](https://docs.shorebird.dev/account/billing/), [Shorebird plans](https://docs.shorebird.dev/account/plans/)
- [music-assistant-client](https://github.com/music-assistant/client)
- [Music Assistant installation](https://www.music-assistant.io/installation/)
- [freesounder architecture](architecture.md)


## Connect, adopt, and room settings (app + player agent)

The Flutter app talks only to the household-host control API. That API
advertises `_freesounder._tcp`. Every board also runs
`services/player-agent` on port 8090 as `_freesounder-player._tcp`.

- Settings in the app lists discovered hosts, accepts a manual address
  (persisted), reconnects, and can switch back to the in-app demo.
- Add player lists unclaimed agents (`GET /api/v1/players/unclaimed`) and
  adopts them (`POST /api/v1/players/adopt`). The host forwards that to the
  agent, which stores the room name, points `snapclient` at the host, and
  restarts it.
- Room settings (`GET`/`PATCH /api/v1/players/{id}/settings`) cover name,
  volume limit, bass, treble, balance, loudness (bass boost) and DAC.
  Tone controls are applied on the Pi with an ALSA LADSPA equaliser.

```bash
cd services/player-agent && uv sync --group dev && uv run ruff check src tests && uv run pytest
```
