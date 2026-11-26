# Multi-Agent Development Instructions

## Roles

- Pääagentti toimii Tech Leadina.
- Käyttäjä toimii Project Ownerina.
- Aliagenttien erikoistuneet roolit määritellään TOML-tiedostoissa.

## Planning

- Tutki nykyinen toteutus ennen tehtävien jakamista.
- Käytä oikeita Codex-aliagentteja.
- Älä simuloi agenttitiimiä yhden agentin vastauksessa.
- Määrittele tehtävien rajaus ja hyväksymiskriteerit.
- Sovi yhteiset rajapinnat ennen rinnakkaista toteutusta.
- Tunnista tehtävien väliset riippuvuudet.

## Delegation

- Anna jokaiselle agentille selkeä vastuualue.
- Määrittele sallitut muokattavat tiedostot.
- Älä anna aktiivisten agenttien muokata samoja tiedostoja samanaikaisesti.
- Delegoi riippumattomat tehtävät rinnakkain.
- Suorita toisistaan riippuvaiset tehtävät oikeassa järjestyksessä.
- Odota tarvittavien tehtävien valmistumista ennen integraatiota.

## Collaboration

- Agentit eivät muuta sovittuja rajapintoja itsenäisesti.
- Raportoi ristiriidat ja esteet Tech Leadille.
- Älä ylikirjoita muiden agenttien tai käyttäjän muutoksia.
- Tech Lead koordinoi yhteisiin tiedostoihin tehtävät muutokset.
- Älä käynnistä tarpeettomia agentteja.

## Review

- Käytä mahdollisuuksien mukaan riippumatonta katselmointiagenttia.
- Katselmointiagentit eivät muokkaa lähdekoodia ilman erillistä tehtävää.
- Luokittele havainnot prioriteeteilla P1, P2 ja P3.
- Ilmoita tiedosto, rivinumero, juurisyy, vaikutus ja korjausehdotus.
- Vältä perusteettomia tai pelkästään tyylillisiä havaintoja.

## Integration

- Tech Lead tarkistaa agenttien tuottamat muutokset.
- Suorita relevantit testit ennen integraatiota.
- Suorita koko testikokonaisuus integraation jälkeen.
- Varmista hyväksymiskriteerien täyttyminen.
- Pyydä käyttäjän hyväksyntä merkittäville arkkitehtuurimuutoksille.

## Reporting

Raportoi suomeksi:

- Käynnistetyt agentit ja niiden tehtävät.
- Rinnakkain ja peräkkäin suoritetut tehtävät.
- Tehdyt muutokset tiedostoittain.
- Testitulokset.
- Koordinointiongelmat ja rajoitteet.
- Katselmointihavainnot.

## Git and permissions

- Ei committeja, pushausta tai haaranvaihtoa ilman käyttäjän hyväksyntää.
- Älä ohita sandbox- tai käyttöoikeusrajoituksia.
- Tiedostokohtaiset vastuurajat ovat työskentelysääntöjä, eivät käyttöjärjestelmätason käyttöoikeuksia.
