// The shell and its routes, exercised through what a user sees and clicks.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import es from "./i18n/es.json";
import { paths } from "./paths";
import { renderAppAt, stubHealthyApi } from "./test/render";

function sectionHeading() {
  return screen.getByRole("heading", { level: 2 });
}

describe("the app shell", () => {
  beforeEach(() => {
    stubHealthyApi();
  });

  it("opens the dashboard at the root address", () => {
    renderAppAt("/");

    expect(sectionHeading()).toHaveTextContent(es.dashboard.title);
    expect(screen.getByText(es.dashboard.empty)).toBeInTheDocument();
  });

  it("names the app in the header", () => {
    renderAppAt(paths.dashboard);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(es.app.name);
  });

  it("links to the two sections from the navigation", () => {
    renderAppAt(paths.dashboard);

    const navigation = screen.getByRole("navigation", { name: es.nav.label });
    const links = within(navigation).getAllByRole("link");

    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      [es.nav.dashboard, paths.dashboard],
      [es.nav.loads, paths.loads],
    ]);
  });

  it("marks the section that is open", () => {
    renderAppAt(paths.loads);

    const navigation = screen.getByRole("navigation", { name: es.nav.label });

    expect(within(navigation).getByRole("link", { name: es.nav.loads })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(navigation).getByRole("link", { name: es.nav.dashboard })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("moves between the sections", async () => {
    const user = userEvent.setup();
    renderAppAt(paths.dashboard);

    await user.click(screen.getByRole("link", { name: es.nav.loads }));
    expect(sectionHeading()).toHaveTextContent(es.loads.title);
    expect(screen.getByText(es.loads.empty)).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: es.nav.dashboard }));
    expect(sectionHeading()).toHaveTextContent(es.dashboard.title);
  });

  it("says so when the address matches no section, and offers the way back", async () => {
    const user = userEvent.setup();
    renderAppAt("/no-such-section");

    expect(sectionHeading()).toHaveTextContent(es.notFound.title);

    await user.click(screen.getByRole("link", { name: es.notFound.back }));
    expect(sectionHeading()).toHaveTextContent(es.dashboard.title);
  });
});
