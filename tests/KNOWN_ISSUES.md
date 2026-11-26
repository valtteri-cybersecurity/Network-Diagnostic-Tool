# Tuotantokoodin tunnetut ongelmat

Alla nimetyt testit tarkistavat vielä avoimien ongelmien nykyisen käyttäytymisen
täsmällisesti. Niiden läpäiseminen ei tarkoita, että käyttäytyminen olisi oikea
tavoite. Virheiden korjaus tulee tehdä erillään rakenteellisesta refaktoroinnista
ja päivittää silloin myös niitä kuvaavat testit.

Prioriteetit: P2 = normaalikäytön virheellinen tai puutteellinen raportointi,
P3 = virheellisen tai poikkeavan syötteen validointipuute. Ei havaittuja P0/P1-ongelmia.
Avoimia numeroituja ongelmia ei tällä hetkellä ole. Jäljellä olevat rajaukset on kuvattu
tiedoston lopussa.

## Korjattu (vaihe 10): Verkkotiedot saattoivat tulla eri oletusreiteiltä (entinen kohta 1)

`parsers.select_default_route()` valitsee yhden oletusreitin ytimen tapaan: pienimmän
metricin, jolloin puuttuva metric tulkitaan nollaksi ja tasatilanteessa valitaan ensimmäinen.
Gateway, liitäntä ja lähdeosoite luetaan kaikki tältä reitiltä. Virheellinen metric tuottaa
`ParsingError`-poikkeuksen.

Testi `test_get_ip_currently_uses_later_default_source_before_first_interface` on korvattu
testillä `test_get_ip_reads_address_from_selected_default_route`. Testit
`test_route_parsers_read_all_values_from_lowest_metric_default_route` ja
`test_select_default_route_*` epäonnistuivat alkuperäisellä toteutuksella.

## Korjattu (vaihe 10): Ulkoisesti yhdistettyä liitäntää ei tunnistettu (entinen kohta 2)

`get_network_plugin()` hyväksyy nyt tilat `connected` ja `connected (externally)`.
Testi `test_get_network_plugin_currently_ignores_externally_connected_interface` on korvattu
testillä `test_get_network_plugin_accepts_externally_connected_interface`.

## Korjattu (vaihe 10): Täydellinen pakettihävikki aikakatkaistiin

Kun vastauksia ei tullut, iputilsin oletusodotus (`-W`, 10 s) kolmen paketin jälkeen
ylitti 10 sekunnin `PING_TIMEOUT`-rajan. Täydellinen hävikki raportoitiin siksi teknisenä
`TIMEOUT`-tilana ilman tilastoja. Ping-komentoon on lisätty `-W 2`, ja aikakatkaisu säilyy
ennallaan. Testi: `test_run_ping_limits_reply_wait_below_command_timeout`.

## Korjattu (vaihe 10): Pingin ICMP-virheet ja gatewayn puuttumisen syy

`+N errors` tallennetaan nyt kenttään `PingResult.icmp_errors`. CLI ei enää väitä kohteen
jättäneen vastaamatta, kun reitittimeltä tuli virheilmoitus, kuten *Destination Host
Unreachable*. Gateway-ping erottaa puuttuvan oletusreitin oletusreitistä, jolla ei ole
gatewayta. Testit: `test_parse_ping_statistics_reports_icmp_error_count`,
`test_display_ping_report_explains_total_loss_with_icmp_errors` ja
`test_ping_gateway_distinguishes_missing_route_from_route_without_gateway`.

## Korjattu (vaihe 10): DNS-tulosten tulkinta

- `getent ahostsv6` käyttää `AI_V4MAPPED`-lippua. Pelkän IPv4:n nimelle raportoitiin siksi
  IPv6-osoitteita (`::ffff:a.b.c.d`). Testi:
  `test_ipv_check_does_not_report_ipv4_mapped_addresses_as_ipv6`.
- digin poistumiskoodi 9 (yksikään DNS-palvelin ei vastannut) nosti poikkeuksen
  `dig failed: exit code 9`. Nyt tulos on `DnsLatencyResult`, jonka tila on `TIMEOUT` tai
  `FAILED` ja `resolved=None`. Testi: `test_dns_latency_reports_no_reply_from_server_as_result`.

## Korjattu (vaihe 10): Palvelunimi väärälle protokollalle

Yksiselitteisesti vain UDP:tä tai vain TCP:tä käyttäville palveluille ei enää ehdoteta
nimeä toisella protokollalla (esim. TCP 68 tai UDP 22). Testi:
`test_analyze_open_ports_names_single_protocol_services_only_for_their_protocol`.

## Korjattu (vaihe 10): Socket-parseri ohitti tunnistamattomat rivit hiljaa

`ss -H` ei tulosta otsikkoa, joten jokaisen ei-tyhjän rivin täytyy jäsentyä. Ohitettu rivi
olisi saanut socket-määrän ja palvelulistan näyttämään täydellisiltä. Tunnistamaton rivi
tuottaa nyt `ParsingError("Unrecognized line in ss output")` -virheen, jonka CLI näyttää
hallittuna virheenä. Vaiheen 9 testi `test_main_does_not_suggest_services_for_invalid_socket_ports`
on muutettu muotoon `test_main_reports_invalid_socket_line_instead_of_partial_service_list`.

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
Itse muodostetut mallit (esim. GUI:sta) eivät kulje parserien kautta.

Vaiheen 11 rajaukset (Service Layer):

- `get_listening_ports()` on edelleen kaikki tai ei mitään. Yksikin tunnistamaton `ss`-rivi
  estää koko socket-listan, koska vaiheen 10 parserisopimusta ei muutettu.
- `dns.ipv_check()` on yhden nimen osalta kaikki tai ei mitään: jos IPv6-haku epäonnistuu,
  saman nimen onnistunut IPv4-tulos katoaa. Service Layer säilyttää muiden nimien tulokset.
- `RoutingSnapshot.errors` tallentaa virheen tekstinä, joten poikkeuksen tyyppi (esim.
  aikakatkaisu) näkyy vain viestissä.
- Python 3.9 -yhteensopivuus on tarkistettu vain syntaksin osalta. Testejä ei ole ajettu
  Python 3.9 -tulkilla.

Vaiheen 10 rajaukset:

- Multipath-oletusreitin (`nexthop`) seuraavan hypyn arvoja ei jäsennetä, joten liitäntä ja
  gateway ovat tällöin `None`. `linkdown`- ja `dead`-lippuja ei huomioida, eikä
  policy routingia arvioida.
- Palvelunimi on edelleen porttiin perustuva ehdotus. Kuuntelevan osoitteen altistusta
  (loopback vai kaikki liitännät) ei luokitella.
- `parse_nmcli_output()` ja `parse_nmcli_states()` ohittavat edelleen virheelliset rivit.
- Kun yhtään vastausta ei ole saatu, ping odottaa viimeisen paketin jälkeen enintään
  `PING_REPLY_WAIT` (2 s). Tätä hitaammat vastaukset lasketaan hävityiksi.

Vaiheen 9 rajaukset:

- Hävikin toleranssi on alle yksi prosenttiyksikkö. Hyvin suurilla pakettimäärillä
  yhden paketin ero voi jäädä toleranssin sisään.
- `mdev`-arvon suhdetta min/max-arvoihin ei tarkisteta.
- Ping-yhteenveto vaatii neljä RTT-arvoa, joten BusyBoxin kolmen arvon muoto
  (`min/avg/max`) tulkitaan virheeksi kuten ennenkin.
- getent `ahosts`-tulosteen virheelliset rivit ohitetaan.
- Jos jäsennys epäonnistuu, tuloksessa ei ole osittaisia mittausarvoja.
