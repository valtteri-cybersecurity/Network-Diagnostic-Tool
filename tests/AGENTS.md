# Testing Instructions

## Test structure

- Käytä pytestiä.
- Yksi testimoduuli kutakin tuotantokoodin moduulia kohden.
- Käytä tavallisia testifunktioita.
- Noudata Arrange–Act–Assert-periaatetta.
- Käytä selkeitä englanninkielisiä testifunktioiden nimiä.

## Test quality

- Testaa onnistuneet tilanteet, virhetilanteet ja raja-arvot.
- Testaa käyttäytymistä, älä tarpeettomasti toteutuksen yksityiskohtia.
- Käytä merkityksellisiä assert-tarkistuksia.
- Älä heikennä testejä tuotantokoodin ongelmien peittämiseksi.
- Älä tavoittele keinotekoisia testimääriä.
- Säilytä olemassa olevien testien tarkoitus refaktoroinnissa.

## Fixtures and mocking

- Hyödynnä olemassa olevaa conftest.py-tiedostoa.
- Säilytä järjestelmäkomentojen automaattinen esto.
- Mockaa riippuvuudet siinä nimiavaruudessa, jossa niitä käytetään.
- Suosi monkeypatch-, capsys- ja pytest.raises()-rakenteita.
- Käytä tests/fixtures/-hakemistoa uudelleenkäytettäville komentotulosteille.
- Älä luo tarpeettomia apufunktioita tai fixture-hierarkioita.

## Test isolation

- Ei todellisia järjestelmäkomentoja.
- Ei todellisia verkkoyhteyksiä tai DNS-kyselyitä.
- Ei riippuvuuksia työaseman verkkoasetuksista.
- Ei root- tai sudo-oikeuksia.
- Ei riippuvuutta testien suoritusjärjestyksestä.
- Käytä determinististä testidataa.
- Estä testien jääminen loputtomiin syötesilmukoihin.

## Regression testing

- Kirjoita tunnetulle bugille regressiotesti ennen korjausta.
- Varmista testin epäonnistuminen alkuperäisellä toteutuksella.
- Varmista testin läpäisy korjauksen jälkeen.
- Päivitä KNOWN_ISSUES.md vain todennetun korjauksen perusteella.
- Perustele poistettavat, yhdistettävät ja muutettavat testit.

## Verification

Suorita koko testikokonaisuus projektin juurihakemistosta:

python -m pytest -v

Käytä README.md-tiedoston ajantasaista kattavuuskomentoa.

Vertaa rivi- ja haarakattavuutta lähtötilanteeseen.

Suorita testit tarvittaessa myös sekoitetussa ja toistettavassa suoritusjärjestyksessä.

Raportoi testimäärät, tulokset, kattavuus, regressiot ja jäljelle jääneet rajoitteet.
