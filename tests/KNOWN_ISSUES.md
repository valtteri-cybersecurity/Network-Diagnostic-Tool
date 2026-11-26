# Tuotantokoodin tunnetut ongelmat

Alla nimetyt testit tarkistavat vielä avoimien ongelmien nykyisen käyttäytymisen
täsmällisesti. Niiden läpäiseminen ei tarkoita, että käyttäytyminen olisi oikea
tavoite. Virheiden korjaus tulee tehdä erillään rakenteellisesta refaktoroinnista
ja päivittää silloin myös niitä kuvaavat testit.

Prioriteetit: P2 = normaalikäytön virheellinen tai puutteellinen raportointi,
P3 = virheellisen tai poikkeavan syötteen validointipuute. Ei havaittuja P0/P1-ongelmia.

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

## 3. P3: Porttinumeroiden aluetta ja tyyppiä ei validoida

`analysis.analyze_open_ports()` hyväksyy portit -1 ja 65536 sekä katkaisee
liukuluvun 22.9 portiksi 22. Tämä voi tuottaa väärän palveluehdotuksen.

Testit: `test_analyze_open_ports_currently_accepts_any_integer_port` ja
`test_analyze_open_ports_currently_truncates_float_port`.

## 4. P3: Mittausarvojen mielekkyyttä ei validoida

`ping.get_ping_statistics()` hyväksyy neljä vastaanotettua pakettia kolmesta
lähetetystä sekä 150 prosentin hävikin. Negatiivinen keskimääräinen RTT ja
minimiä pienempi maksimi voivat tuottaa tilan `COMPLETED`. Analysis-moduulin
viiveluokittelut hyväksyvät negatiiviset ajat, ja käänteinen RTT-väli saa
luokituksen `Excellent`.

Testit: `test_get_ping_statistics_currently_accepts_impossible_packet_counts_and_loss`,
`test_get_ping_statistics_currently_accepts_negative_and_reversed_rtt`,
`test_latency_classifiers_currently_accept_negative_measurements` ja
`test_analyze_jitter_currently_accepts_reversed_measurements`.

## 5. P3: Osoite- ja socket-tulosteiden validointi on puutteellinen

`dns.dns_query()` tulkitsee minkä tahansa ei-tyhjän getent-tulosteen onnistuneeksi,
kun paluuarvokoodi on 0. `not-an-address example.com` tuottaa arvot
`execution_status=COMPLETED`, `resolved=True` ja
`ip_address="not-an-address"`. `network.get_ip()` hyväksyy vastaavasti
virheellisen `src`-arvon. `parse_ss_output()` voi säilyttää virheellisen osoitteen
ja ei-numeerisen portin, jos rivillä on oikea kenttämäärä ja kaksoispisteet.

Testit: `test_dns_query_currently_accepts_invalid_address_with_success_code`,
`test_get_ip_currently_accepts_invalid_source_address` ja
`test_parse_ss_output_currently_keeps_invalid_ports_and_unknown_protocols`.
Tuntemattoman protokollan säilyttäminen itsessään ei ole virhe: suodatus tehdään
analysis-moduulissa.

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
Tietomallien tyyppivihjeet eivät validoi mittausarvoja ajon aikana: yllä kuvatut
validointipuutteet sekä reititys- ja NetworkManager-ongelmat säilyvät tämän tehtävän
rajauksen mukaisesti.
