# Tuotantokoodin tunnetut ongelmat

Alla nimetyt testit tarkistavat vielä avoimien ongelmien nykyisen käyttäytymisen
täsmällisesti. Niiden läpäiseminen ei tarkoita, että käyttäytyminen olisi oikea
tavoite. Virheiden korjaus tulee tehdä erillään rakenteellisesta refaktoroinnista
ja päivittää silloin myös niitä kuvaavat testit.

Prioriteetit: P2 = normaalikäytön virheellinen tai puutteellinen raportointi,
P3 = virheellisen tai poikkeavan syötteen validointipuute. Ei havaittuja P0/P1-ongelmia.
Avoimet kohdat 1 ja 2 liittyvät oletusreitin valintaan ja yhteystilan tulkintaan, ja ne
kuuluvat vaiheeseen 10.

## 1. P2: Verkkotiedot voivat yhdistyä eri oletusreiteiltä

`network.get_ip()` etsii lähdeosoitetta kaikista oletusreiteistä ennen ensimmäisen
verkkoliitännän osoitekyselyä. Jos ensimmäiseltä reitiltä puuttuu `src` ja toisella
on se, IP tulee toiselta reitiltä, mutta `get_nic()` ja `get_gateway()` palauttavat
ensimmäisen reitin tiedot. Järjestelmän tilannekuva voi olla ristiriitainen.

Testi: `test_get_ip_currently_uses_later_default_source_before_first_interface`.

## 2. P2: Ulkoisesti yhdistettyä verkkoliitäntää ei tunnisteta

`network.get_network_plugin()` hyväksyy vain täsmälleen tilan `connected`.
Muun kuin loopback-liitännän `connected (externally)` palauttaa arvon `False`,
jolloin päävalikko ilmoittaa, ettei yhdistettyä verkkoliitäntää havaittu.

Testi: `test_get_network_plugin_currently_ignores_externally_connected_interface`.

## Korjattu (vaihe 9): Porttinumeroiden aluetta ja tyyppiä ei validoitu

`analysis.analyze_open_ports()` hyväksyi portit -1, 0 ja 65536 sekä katkaisi
liukuluvun 22.9 portiksi 22. Palveluehdotus hyväksyy nyt vain portin 1–65535
kokonaislukuna tai desimaalimerkkijonona. `parsers.parse_ss_output()` ohittaa rivit,
joiden portti ei ole `*` tai 0–65535.

Regressiotestit `test_analyze_open_ports_skips_invalid_or_out_of_range_ports` ja
`test_main_does_not_suggest_services_for_invalid_socket_ports` epäonnistuivat
alkuperäisellä toteutuksella ja läpäisevät korjauksen jälkeen.

## Korjattu (vaihe 9): Mittausarvojen mielekkyyttä ei validoitu

Ping-tulosteen neljä vastaanotettua pakettia kolmesta lähetetystä, 150 prosentin
hävikki, pakettimääriin sopimaton hävikki, negatiiviset, NaN- ja äärettömät RTT-arvot
sekä järjestys, jossa min > avg tai avg > max, tuottavat nyt `ParsingError`-poikkeuksen.
`ping_target()` muuntaa sen `FAILED`-tulokseksi. Viiveluokittelijat ja RTT-vaihteluväli
nostavat `ValueError`-poikkeuksen negatiivisesta, ei-äärellisestä tai käänteisestä syötteestä.

Regressiotestit `test_ping_target_rejects_implausible_or_contradictory_statistics`,
`test_latency_classifiers_reject_negative_or_non_finite_measurements` ja
`test_analyze_jitter_rejects_reversed_negative_or_non_finite_measurements`
epäonnistuivat alkuperäisellä toteutuksella ja läpäisevät korjauksen jälkeen.

## Korjattu (vaihe 9): Osoite- ja socket-tulosteiden validointi oli puutteellinen

`dns.dns_query()` tuottaa nyt virheellisestä getent-osoitteesta tuloksen
`execution_status=FAILED` ja virheen `Invalid address in getent output`.
`network.get_ip()` ja `get_gateway()` sekä `get_nic()` nostavat `ParsingError`-poikkeuksen
virheellisestä `src`-, `via`- tai `dev`-arvosta. `parse_ss_output()` ohittaa rivit, joilla
on virheellinen osoite tai portti. Virheelliset dig-kenttien arvot tuottavat `FAILED`-tuloksen.
Tuntemattoman protokollan säilyttäminen ei edelleenkään ole virhe: suodatus tehdään
analysis-moduulissa.

Regressiotestit `test_dns_query_reports_invalid_getent_address_as_failed_result`,
`test_dns_latency_reports_invalid_field_values_as_failed_result`,
`test_route_values_are_validated_before_use`,
`test_get_ip_rejects_invalid_interface_address_output` ja
`test_main_reports_invalid_route_source_address_as_controlled_error` epäonnistuivat
alkuperäisellä toteutuksella ja läpäisevät korjauksen jälkeen.

## Korjattu: Pingin stdout peitti teknisen suoritusvirheen

Yhteinen Command Runner hyväksyy pingille vain paluuarvot 0 ja 1. Koodi 2 ja
muut odottamattomat koodit käsitellään virheinä myös stdoutin sisältäessä tekstiä.
Pingin tulosrajapinta välittää olennaisen virheviestin `PingResult.error`-kentässä
käyttöliittymälle.

`test_run_ping_reports_error_returncode_even_with_stdout` epäonnistui alkuperäisellä
ping-toteutuksella: se palautti stdout-otsakkeen odotetun virheilmoituksen sijaan.
Sama testi läpäisi integraation jälkeen koodeilla 2, 3 ja -15. Myös
`test_run_ping_diagnostic_returns_technical_failure_instead_of_stdout_statistics` varmistaa,
että tekninen virhe säilyy tulosoliossa eikä stdoutin onnistuneita tilastoja käytetä.
CLI-esityksen varmistaa `test_main_reports_ping_technical_failure_instead_of_stdout_statistics`.
Paluuarvon 1 tavanomainen pakettihävikki on edelleen hyväksytty diagnostiikkatulos.

## Korjattu: Täydellinen pakettihävikki luokiteltiin tekniseksi epäonnistumiseksi

`test_total_packet_loss_is_a_completed_measurement` kirjoitettiin ennen muutosta.
Se epäonnistui alkuperäisellä toteutuksella, joka palautti tilan `failed` ja
virheen `No latency data available`. Rakenteinen tulos palauttaa nyt tilan
`COMPLETED`, `reachable=False`, 100 prosentin hävikin ja puuttuvat RTT-arvot
arvoina `None`. ICMP-vastausten puuttuminen ei todista internetyhteyden katkeamista.
Aikakatkaisu pysyy erillisenä `TIMEOUT`-tilana myös silloin, kun osittainen stdout
sisältää jäsennettäviä tilastoja.

## Nykyisen vaiheen rajaukset ja jäljellä oleva tekninen velka

`PING_ERROR:`-merkkijonoihin perustuva teknisten virheiden välittäminen ja tulkinta
on poistettu. Pingin apufunktiot nostavat poikkeuksia, ja `ping_target()` muuntaa
ne kerran rakenteiseksi tulokseksi. DNS:n ja verkkodiagnostiikan tekniset
komentovirheet nostetaan edelleen poikkeuksina nykyisen sopimuksen mukaisesti.

CLI-riippuvuus on poistettu: `run_ping_diagnostic()` korvaa `handle_ping()`-funktion
ja palauttaa mittaukset sekä analyysit `PingResult`-oliossa. Main välittää olion
Output-moduulille, joka esittää valmiit tiedot muuttamatta niitä. Pakettihävikin
luokittelu kuuluu nyt Analysis-moduuliin, ja selitystekstit jäävät käyttöliittymään.
Tietomallien tyyppivihjeet eivät validoi mittausarvoja ajon aikana. Vaiheesta 9 lähtien
komentotulosteet validoidaan `parsers.py`-moduulissa ennen tietomallien muodostamista.
Itse muodostetut mallit (esim. GUI:sta) eivät kulje parserien kautta. Reititys- ja
NetworkManager-ongelmat 1–2 säilyvät vaiheen 9 rajauksen mukaisesti.

Vaiheen 9 rajaukset:

- Hävikin toleranssi on alle yksi prosenttiyksikkö. Hyvin suurilla pakettimäärillä
  yhden paketin ero voi jäädä toleranssin sisään.
- `mdev`-arvon suhdetta min/max-arvoihin ei tarkisteta.
- Ping-yhteenveto vaatii neljä RTT-arvoa, joten BusyBoxin kolmen arvon muoto
  (`min/avg/max`) tulkitaan virheeksi kuten ennenkin.
- getent `ahosts`-tulosteen virheelliset rivit ohitetaan.
- Jos jäsennys epäonnistuu, tuloksessa ei ole osittaisia mittausarvoja.
