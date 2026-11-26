# Network Diagnostic Tool

Linux-verkkodiagnostiikkasovellus. Nykyinen CLI ja tuleva GUI käyttävät samaa diagnostiikkamoottoria.

## Language

- Keskustelut ja raportit suomeksi.
- Lähdekoodi, tunnisteet ja tekninen dokumentaatio englanniksi.

## Coding standards

- Käytä yksinkertaista, helposti ymmärrettävää Pythonia.
- Suosi tavallisia if/else-rakenteita ja for-silmukoita.
- Käytä tarkoituksenmukaisia tyyppivihjeitä.
- Apufunktiot nostavat tarkoituksenmukaisia poikkeuksia.
- Vältä tarpeettomia abstraktioita ja toistoa.
- Säilytä Python 3.9 -yhteensopivuus.
- Älä lisää riippuvuuksia ilman hyväksyntää.

## Architecture

- Diagnostiikkamoottori ei riipu käyttöliittymästä.
- Diagnostiikkakomennot käyttävät yhteistä Command Runneria.
- Käytä models.py-tiedoston rakenteisia tietomalleja.
- Käytä exceptions.py-tiedoston yhteisiä poikkeuksia.
- Erota tekninen suoritusvirhe varsinaisesta mittaustuloksesta.

## Security

- Ei shell=True-asetusta.
- Validoi ulkoiset syötteet ennen komentojen suorittamista.
- Säilytä komentojen aikakatkaisut.
- Ei tarpeettomia käyttöoikeuksia tai sudo-komentoja.
- Käytä vain valmistajien virallisia asennusohjeita ja pakettilähteitä.

## Workflow

- Lue nykyinen toteutus ennen muutoksia.
- Noudata tehtävän rajausta.
- Tee mahdollisimman pieniä, perusteltuja muutoksia.
- Tarkista tests/KNOWN_ISSUES.md.
- Testaa ennen muutoksia ja niiden jälkeen.
- Päivitä dokumentaatio rajapintojen muuttuessa.
- Raportoi havaitut ongelmat ja rajoitteet.

## Git

- Käytä fast-forward-only-käytäntöä.
- Ei committeja, pushausta tai haaranvaihtoa ilman hyväksyntää.
- Älä ylikirjoita käyttäjän olemassa olevia muutoksia.

## Scoped instructions

- Testaustehtävät: lue tests/AGENTS.md.
- Agenttien koordinointi ja määritykset: lue .codex/agents/AGENTS.md.
- Noudata alahakemistojen tarkempia ohjeita.
