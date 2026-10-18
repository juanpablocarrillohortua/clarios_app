// The frame every page sits in: the header with the signed-in user, the
// navigation between the sections that user may open, and the line that
// reports whether the API answers.
//
// On a wide screen everything sits in the header, so the pages (the report
// above all) get the rest of the window: the sections are tabs, the API's
// state is a dot, and the open page may put an action there (HeaderAction).
// On a narrow screen the sections are a drawer the burger opens, and the API's
// line is the footer.
import { AppShell, Burger, Group, NavLink, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Outlet, useLocation } from "react-router";

import { useSession } from "../auth/Session";
import { sections } from "../sections";
import { ApiStatus } from "./ApiStatus";
import { HeaderActionSlot } from "./HeaderAction";
import { UserMenu } from "./UserMenu";
import { useWideLayout } from "./useWideLayout";

export function Shell() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { me } = useSession();
  const wide = useWideLayout();
  const [opened, { toggle, close }] = useDisclosure(false);
  // The header's place for the open page's actions.
  const [actionSlot, setActionSlot] = useState<HTMLDivElement | null>(null);

  const allowed = sections.filter((section) => me.permissions.includes(section.permission));

  const links = allowed.map((section) => {
    const active = pathname === section.path || pathname.startsWith(`${section.path}/`);
    return (
      <NavLink
        key={section.path}
        component={Link}
        to={section.path}
        label={t(section.label)}
        active={active}
        aria-current={active ? "page" : undefined}
        onClick={close}
        // In the header the links are tabs, side by side.
        {...(wide ? { w: "auto", mb: 0, noWrap: true } : {})}
      />
    );
  });

  return (
    <HeaderActionSlot.Provider value={wide ? actionSlot : null}>
      <AppShell
        header={{ height: 56 }}
        navbar={wide ? undefined : { width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }}
        footer={wide ? undefined : { height: 40 }}
        padding="sm"
      >
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between" wrap="nowrap">
            <Group wrap="nowrap" gap="lg">
              {!wide && (
                <Burger opened={opened} onClick={toggle} size="sm" aria-label={t("nav.toggle")} />
              )}
              <Title order={1} size="h4">
                {t("app.name")}
              </Title>
              {wide && (
                <nav aria-label={t("nav.label")}>
                  <Group gap="xs" wrap="nowrap">
                    {links}
                  </Group>
                </nav>
              )}
            </Group>
            <Group gap="md" wrap="nowrap">
              {/* What the page puts here joins this row as if it were written here. */}
              {wide && <div ref={setActionSlot} style={{ display: "contents" }} />}
              {wide && <ApiStatus compact />}
              <UserMenu />
            </Group>
          </Group>
        </AppShell.Header>

        {!wide && (
          <AppShell.Navbar p="sm" aria-label={t("nav.label")}>
            {links}
          </AppShell.Navbar>
        )}

        <AppShell.Main>
          <Outlet />
        </AppShell.Main>

        {!wide && (
          <AppShell.Footer px="md">
            <Group h="100%">
              <ApiStatus />
            </Group>
          </AppShell.Footer>
        )}
      </AppShell>
    </HeaderActionSlot.Provider>
  );
}
