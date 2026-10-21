// The shell and its routes, exercised through what a signed-in user sees and
// clicks. The user here is an operator, who may open every section; what other
// users see is in auth/Session.test.tsx.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import es from "./i18n/es.json";
import { paths } from "./paths";
import { onWideScreen, renderAppAt, stubApi } from "./test/render";

function sectionHeading() {
  return screen.findByRole("heading", { level: 2 });
}

function navigation() {
  return screen.findByRole("navigation", { name: es.nav.label });
}

describe("the app shell", () => {
  beforeEach(() => {
    stubApi();
  });

  it("opens the dashboard at the root address", async () => {
    renderAppAt("/");

    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
    // What the dashboard shows is in pages/DashboardPage.test.tsx.
    expect(screen.getByRole("link", { name: es.dashboard.openInPowerBi })).toBeInTheDocument();
  });

  it("names the app in the header", async () => {
    renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(es.app.name);
  });

  it("links to the three sections from the navigation", async () => {
    renderAppAt(paths.dashboard);

    const links = within(await navigation()).getAllByRole("link");

    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      [es.nav.dashboard, paths.dashboard],
      [es.nav.models, paths.models],
      [es.nav.loads, paths.loads],
    ]);
  });

  it("marks the section that is open", async () => {
    renderAppAt(paths.loads);

    const links = within(await navigation());

    expect(links.getByRole("link", { name: es.nav.loads })).toHaveAttribute("aria-current", "page");
    expect(links.getByRole("link", { name: es.nav.dashboard })).not.toHaveAttribute("aria-current");
  });

  it("moves between the sections", async () => {
    const user = userEvent.setup();
    renderAppAt(paths.dashboard);

    await user.click(await screen.findByRole("link", { name: es.nav.loads }));
    expect(await sectionHeading()).toHaveTextContent(es.loads.title);
    expect(screen.getByText(es.loads.intro)).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: es.nav.dashboard }));
    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });

  it("says so when the address matches no section, and offers the way back", async () => {
    const user = userEvent.setup();
    renderAppAt("/no-such-section");

    expect(await sectionHeading()).toHaveTextContent(es.notFound.title);

    await user.click(screen.getByRole("link", { name: es.notFound.back }));
    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });

  it("on a narrow screen, keeps the sections in a drawer and the API's line in the footer", async () => {
    renderAppAt(paths.dashboard);

    const header = await screen.findByRole("banner");
    expect(within(header).getByRole("button", { name: es.nav.toggle })).toBeInTheDocument();
    expect(within(header).queryByRole("navigation")).toBeNull();
    expect(within(screen.getByRole("contentinfo")).getByRole("status")).toBeInTheDocument();
  });
});

describe("the app shell on a wide screen", () => {
  beforeEach(() => {
    onWideScreen();
    stubApi();
  });

  it("puts the sections in the header as tabs, with no drawer to open", async () => {
    renderAppAt(paths.loads);

    const header = await screen.findByRole("banner");
    const links = within(within(header).getByRole("navigation", { name: es.nav.label }));

    expect(links.getAllByRole("link").map((link) => link.textContent)).toEqual([
      es.nav.dashboard,
      es.nav.models,
      es.nav.loads,
    ]);
    expect(links.getByRole("link", { name: es.nav.loads })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: es.nav.toggle })).toBeNull();
  });

  it("gives the report the room: no footer, the API's state in the header", async () => {
    renderAppAt(paths.dashboard);

    const header = await screen.findByRole("banner");

    expect(within(header).getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("contentinfo")).toBeNull();
  });

  it("moves the dashboard's link to Power BI into the header, and keeps the section's heading", async () => {
    renderAppAt(paths.dashboard);

    const header = await screen.findByRole("banner");

    expect(
      await within(header).findByRole("link", { name: es.dashboard.openInPowerBi }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: es.dashboard.openInPowerBi })).toHaveLength(1);
    // Out of sight, for screen readers: the tab already names the section.
    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });

  it("takes the link away from the header when the dashboard closes", async () => {
    const user = userEvent.setup();
    renderAppAt(paths.dashboard);
    const header = await screen.findByRole("banner");
    await within(header).findByRole("link", { name: es.dashboard.openInPowerBi });

    await user.click(within(header).getByRole("link", { name: es.nav.loads }));

    expect(await sectionHeading()).toHaveTextContent(es.loads.title);
    expect(screen.queryByRole("link", { name: es.dashboard.openInPowerBi })).toBeNull();
  });
});
