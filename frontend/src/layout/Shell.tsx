// The frame every page sits in: the header, the navigation between the two
// sections, and a footer that reports whether the API answers.
import { AppShell, Burger, Group, NavLink, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useTranslation } from "react-i18next";
import { Link, Outlet, useLocation } from "react-router";

import { paths } from "../paths";
import { ApiStatus } from "./ApiStatus";

export function Shell() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  // On a narrow screen the navigation is a drawer the burger opens.
  const [opened, { toggle, close }] = useDisclosure(false);

  const sections = [
    { to: paths.dashboard, label: t("nav.dashboard") },
    { to: paths.loads, label: t("nav.loads") },
  ];

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }}
      footer={{ height: 40 }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md">
          <Burger
            opened={opened}
            onClick={toggle}
            hiddenFrom="sm"
            size="sm"
            aria-label={t("nav.toggle")}
          />
          <Title order={1} size="h4">
            {t("app.name")}
          </Title>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm" aria-label={t("nav.label")}>
        {sections.map((section) => {
          const active = pathname === section.to || pathname.startsWith(`${section.to}/`);
          return (
            <NavLink
              key={section.to}
              component={Link}
              to={section.to}
              label={section.label}
              active={active}
              aria-current={active ? "page" : undefined}
              onClick={close}
            />
          );
        })}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>

      <AppShell.Footer px="md">
        <Group h="100%">
          <ApiStatus />
        </Group>
      </AppShell.Footer>
    </AppShell>
  );
}
