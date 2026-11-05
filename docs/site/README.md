# Project page

Static one-page site for Avatar Democracy. It is HTML and CSS in this directory (`index.html`, `style.css`, `favicon.svg`). There is no build step, no JavaScript, and no request to a font host, CDN, analytics service, or other third party. The page sets no cookies.

Nothing here is deployed. No host, form, or mailing list is connected. Do not connect one until the project owner approves it.

## Preview locally

From the repository root:

```bash
cd docs/site
python -m http.server 8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Python 3.10 or newer, which this project already requires, is enough. This only serves the files in this directory. It does not start the prototype. The prototype is still `python main.py` from the repository root.

## Hosting

The page can stay in the repo with no host at all. Publishing it is a separate decision.

**GitHub Pages, from `/docs/site`.** Prefer a Pages workflow, or a branch whose root is this directory, so the site root is `index.html` and not the parent `docs/` folder. GitHub's "Deploy from a branch" setting only offers the repository root or `/docs`. Choosing `/docs` on `main` would publish this page at `/site/` and would also publish this README.

This repository is **private**. GitHub Pages on a private repository needs a paid GitHub plan (Pro, Team, or Enterprise). On the free plan, Pages is available only if the repository is made public. Making the repository public publishes the code and the white paper, not only this page.

No Pages workflow is committed. Do not add one that runs on `push` or `pull_request`. If a workflow is added later, it should be `workflow_dispatch` only: checkout, upload `docs/site` with `actions/upload-pages-artifact`, then deploy with `actions/deploy-pages`. Use the current major versions of those actions. Enabling Pages in the repository settings is what actually publishes the site, and it should wait for the owner's approval. A copy of the workflow that is committed but disabled (the file name ignored by Actions, or the workflow turned off in the Actions tab) is the other acceptable form. An automatic deploy is not.

**A free static host, if Pages is not available.** [Cloudflare Pages](https://pages.cloudflare.com/) and [Netlify](https://www.netlify.com/) both have a free tier that can serve this directory with no build command. Set the publish directory to `docs/site`. Connecting either product to a private repository gives that host access to the repo, and the resulting URL is public even while GitHub stays private. Leave the host's analytics and cookie banners off. Turning them on adds third-party requests and cookies, which this page currently does not have.

## A mailing list later (v2)

This page has no email field. The v1 feedback path is [GitHub Issues](https://github.com/spiry2s/avatar-democracy/issues). Who can see that feedback depends on who can see the repository: it is public if the repository is public, and while the repository is private only collaborators can see it.

A later version can add a signup only after the project owner approves the provider, the wording, and a privacy policy. Whichever provider is chosen, all four of these are required before the form goes on the page:

- Double opt-in, so an address is not subscribed until the person confirms it.
- The consent checkbox unticked by default. A pre-ticked box is not consent.
- A named data controller and a contact address, written in the privacy policy.
- A privacy policy linked from the form, covering what is collected (the email address, and nothing else for this list), why, how long it is kept, and how to unsubscribe.

Plans and data regions change. Re-check the vendor's current pricing and subprocessor list before anyone's address is collected. Figures below were taken from the vendors' own pages in November 2026.

- **Buttondown.** Free for the first 100 subscribers ([pricing](https://buttondown.com/pricing)). Buttondown is a US company and stores subscriber data in the United States (AWS `us-west-2`, Oregon), which it says in its [subprocessor list](https://buttondown.com/legal/subprocessors). It publishes a [GDPR page](https://buttondown.com/legal/gdpr-eu-compliance) and a data-processing agreement, and it uses Standard Contractual Clauses for the transfer. This is the option that does **not** keep the list in the EU. Use it only if a US transfer on those terms is acceptable.
- **listmonk, run in the EU.** [listmonk](https://listmonk.app/) is free and open source, and a list can be set to double opt-in. EU handling comes from where it runs: put it on a server in the EU that the project controls, and sign a data-processing agreement with that host. The project is the controller. A hosted form on the project's own domain can stay same-origin, which a third-party embed cannot.
- **Brevo.** French company. The free plan, once the account is approved for sending, can send up to 300 emails a day ([pricing FAQ](https://www.brevo.com/pricing/)). The terms include a data-processing agreement. Confirm on Brevo's current data-storage page and subprocessor list that the account's database region is the EU, and read which subprocessors sit outside it, before collecting addresses.
- **MailerLite.** The free plan covers up to 250 subscribers and 2,500 emails a month, and MailerLite states that it has GDPR controls ([pricing](https://www.mailerlite.com/pricing)). Confirm the account's data region before collecting addresses, and prefer an EU region if the account offers one.

A Brevo, MailerLite, or Buttondown form usually posts to their domain or loads their script. That is a third-party request. It does not belong on this page until the privacy policy is published and the owner has agreed to that request. None of these accounts is connected now.
