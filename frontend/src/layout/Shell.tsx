// The frame every page sits in: the header with the signed-in user, the
// navigation between the sections that user may open, and a footer that
// reports whether the API answers.
import { AppShell, Burger, Group, NavLink, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useTranslation } from "react-i18next";
import { Link, Outlet, useLocation } from "react-router";

import { useSession } from "../auth/Session";
import { sections } from "../sections";
import { ApiStatus } from "./ApiStatus";
import { UserMenu } from "./UserMenu";

export function Shell() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { me } = useSession();
  // On a narrow screen the navigation is a drawer the burger opens.
  const [opened, { toggle, close }] = useDisclosure(false);

  const allowed = sections.filter((section) => me.permissions.includes(section.permission));

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }}
      footer={{ height: 40 }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group wrap="nowrap">
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
          <UserMenu />
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm" aria-label={t("nav.label")}>
        {allowed.map((section) => {
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
