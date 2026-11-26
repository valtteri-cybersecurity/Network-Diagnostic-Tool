# Tuotantokoodin tunnetut ongelmat

Alla nimetyt testit tarkistavat vielä avoimien ongelmien nykyisen käyttäytymisen
täsmällisesti. Niiden läpäiseminen ei tarkoita, että käyttäytyminen olisi oikea
tavoite. Virheiden korjaus tulee tehdä erillään rakenteellisesta refaktoroinnista
ja päivittää silloin myös niitä kuvaavat testit.

Prioriteetit: P2 = normaalikäytön virheellinen tai puutteellinen raportointi,
P3 = virheellisen tai poikkeavan syötteen validointipuute. Ei havaittuja P0/P1-ongelmia.
Avoimia numeroituja ongelmia ei tällä hetkellä ole. Jäljellä olevat rajaukset on kuvattu
tiedoston lopussa.

## Korjattu (vaihe 13): Virheellinen tavu komentotulosteessa kaatoi ohjelman (P2)

`run_command()` dekoodasi tulosteen tiukasti localen merkistöllä. Yksi virheellinen
UTF-8-tavu (esim. `/etc/hosts`-alias tai yhteysnimi) nosti `UnicodeDecodeError`-poikkeuksen,
jota mikään `DiagnosticError`-käsittelijä ei ottanut kiinni. Tuloste dekoodataan nyt
UTF-8:na ja virheelliset tavut korvataan merkillä U+FFFD. Testi:
`test_run_command_replaces_undecodable_output_instead_of_raising`.

## Korjattu (vaihe 13): Oletusreitin valinta `dead`- ja multipath-reiteillä (P2, entinen rajaus)

- `dead`-lipulla merkitty reitti ohitetaan, koska ydin ei käytä sitä. Testit:
  `test_select_default_route_skips_dead_route_with_lower_metric`,
  `test_select_default_route_returns_none_when_only_dead_default_routes_exist`.
- Valittu oletusreitti ilman `dev`-kenttää (multipath `nexthop`, nexthop-ryhmä `nhid`)
  tuottaa `ParsingError`-virheen. Aiemmin tilannekuva näytti liitännän, osoitteen ja
  gatewayn puuttuvina ilman virhettä, ja gateway-ping väitti, ettei reitillä ole gatewayta.
  Testit: `test_route_parsers_reject_default_route_without_single_next_hop`,
  `test_ping_gateway_reports_multipath_route_as_unsupported_instead_of_missing_gateway`,
  `test_get_routing_snapshot_records_multipath_default_route_as_error`.
  Epärealistiset testisyötteet `["default"]` ja `["default via 192.0.2.1"]` siirrettiin
  odottamaan virhettä `None`-tuloksen sijaan.

## Korjattu (vaihe 13): nmcli-parserit ohittivat tunnistamattomat rivit (P2, entinen rajaus)

`parse_nmcli_output()` ja `parse_nmcli_states()` nostavat nyt
`ParsingError("Unrecognized line in nmcli output")` ei-tyhjälle riville, jossa on väärä
kenttämäärä tai puuttuva laite tai tila. Aiemmin tuntematon tuloste raportoitiin tilana
"No connected interface detected". Testit:
`test_parse_nmcli_output_rejects_unrecognized_line_instead_of_partial_list`,
`test_parse_nmcli_states_rejects_unrecognized_line`,
`test_get_connection_status_reports_unrecognized_nmcli_output_as_error`,
`test_get_network_plugin_reports_unrecognized_output_instead_of_guessing`.
Ohitusta kuvanneet testit muutettiin testaamaan tyhjien rivien ohitusta ja tyhjien
TYPE/CONNECTION-kenttien hyväksymistä.

## Korjattu (vaihe 13): Päätteen ohjausmerkit CLI-tulosteessa (P3)

CLI näyttää komennoista ja järjestelmästä tulevan tekstin tulostumattomat merkit
(tabia lukuun ottamatta) escape-muodossa (`\x1b`). Testi:
`test_outputs_show_terminal_control_characters_as_escapes`.

## Korjattu (vaihe 13): Liian vahvat CLI-ilmoitukset (P3)

- Päävalikon tila on nyt "NetworkManager reports no connected interface" (aiemmin
  "No connected interface detected"): arvo kuvaa vain NetworkManagerin näkemystä.
- DNS-viivetesti kertoo, että viive mitattiin määritettyyn DNS-palvelimeen, joka voi
  olla paikallinen välimuisti.

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
- Python-versiot (vaihe 13.1): vähimmäisversio 3.11 (`requires-python = ">=3.11"`),
  ensisijainen kehitysversio ja Docker-ympäristö 3.12. Paikallisesti testit on ajettu vain
  Python 3.12:lla. Vaiheen 13.2 CI-putki ajaa testit versioilla 3.11–3.14; yhteensopivuus on
  todennettu vasta, kun nämä GitHub Actions -ajot ovat onnistuneet. Docker-ympäristön
  versiotestaus on edelleen erillinen tehtävä.

Vaiheen 13.2 rajaukset (CI):

- CI ajaa vain mockatut testit Ubuntu 24.04:llä. Todellista verkkoa, muita jakeluita, Dockeria
  tai päätteen esitystä ei testata.
- pip-audit tunnistaa vain julkisissa haavoittuvuustietokannoissa (PyPI/OSV) olevat
  haavoittuvuudet. Linux-järjestelmätyökaluja (`ping`, `ip`, `dig` ym.) ei auditoida.
- pip-auditin ja CI:n pip-version kiinnitykset (`PIP_AUDIT_VERSION`, `PIP_VERSION`) päivitetään
  käsin; Dependabot ei kata niitä.
- Branch protection ja vaadittu status check (`CI Quality Gate`) on otettava käyttöön
  GitHubin asetuksista käsin ensimmäisen onnistuneen ajon jälkeen.

Vaiheen 12 rajaukset (paketointi):

- Dockerfile ei asenna pakettia. Käyttäjän kehityskontissa testit läpäisevät, kun paketti
  asennetaan erikseen (metatiedot) ja `PYTHONPATH=/app/src`. Tämä ei ole puhdas
  wheel-asennuksen testi. Dockerfilen päivitys kuuluu erilliseen Docker-tehtävään.
- Paketin rakentaminen lataa setuptoolsin PyPI:stä, jos sitä ei ole saatavilla.

Vaiheen 10 rajaukset:

- Multipath-oletusreitti raportoidaan virheenä (vaihe 13); yksittäistä seuraavaa hyppyä ei
  valita. Laajempi multipath-tuki on suunniteltu vaiheeseen 37 (Advanced Diagnostic
  Collection). Policy routingia (`ip rule`) ei arvioida, koska `ip -4 route` näyttää vain
  main-taulun; suunniteltu vaiheeseen 37. `linkdown`-reitit ovat ehdokkaita, kuten ytimen oletusasetuksella
  (`ignore_routes_with_linkdown=0`); jos asetus on käytössä, valinta voi poiketa ytimestä.
  Täsmällinen ratkaisu olisi `ip -4 route get <kohde>` (uusi ominaisuus).
- Palvelunimi on edelleen porttiin perustuva ehdotus. Kuuntelevan osoitteen altistusta
  (loopback vai kaikki liitännät) ei luokitella (vaatisi uuden tietomallikentän).
  Luokittelu on suunniteltu vaiheeseen 16 (Rule-based Root Cause Analysis). Wildcard-osoitteeseen
  (`0.0.0.0`, `::`, `*`) sidottu socket ei yksin todista ulkoista saavutettavuutta tai
  tietoturvahaavoittuvuutta: palomuuri, reititys ja palvelun oma pääsynhallinta ratkaisevat sen.
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

Vaiheen 13 rajaukset:

- `getent hosts` palauttaa koodin 2 sekä olemattomalle nimelle että tavoittamattomalle
  resolverille. `DnsQueryResult.resolved=False` tarkoittaa "ei selvitetty", ei NXDOMAINia.
  Sama koskee `ipv_check()`-tulosten tyhjiä osoitelistoja. getent-haun epäonnistuminen ei
  yksin todista DNS-palvelimen toimintahäiriötä (syy voi olla myös olematon nimi,
  `/etc/nsswitch.conf`, paikallinen resolveri tai verkkoyhteys). Tulkinnan tarkentaminen on
  suunniteltu vaiheeseen 16.
- DNS-viivetulos ei kerro vastanneen palvelimen osoitetta (dig `;; SERVER:`); kentän
  lisääminen on julkisen tietomallin muutos.
- `output.clear_screen()` käyttää `os.system("clear")`. Komento on vakio eikä sisällä
  syötettä; muutosta ei tehty.
- Command Runner lukee koko tulosteen muistiin. Käytettyjen komentojen tulosteet ovat pieniä,
  joten kokorajoitusta ei toteutettu.
- Palvelufunktioita ei voi keskeyttää kesken komennon. Pahin kesto nykyisillä
  aikakatkaisuilla: `lookup_ip_addresses()` 80 s (oletusnimet), `get_routing_snapshot()`
  30 s, gateway-ping 20 s, muut 10 s. Keskeytys on suunniteltu vaiheeseen 24
  (Workers & Application State).
- `cli/main.py`:n neljä kattamatonta haaraa (90->72, 112->95, 115->35, 138->116) ovat
  saavuttamattomia: `read_menu_choice(n)` palauttaa vain arvot 1..n, ja jokainen käsitellään.
- Todellisen Linux-ympäristön hyväksyntätestaus puuttuu edelleen; ohje:
  `docs/testing/manual-acceptance-tests.md` (paikallinen, ei versionhallinnassa).

Suunnitellut käsittelyvaiheet (roadmap `docs/plans/project-roadmap.md`, paikallinen):

| Avoin rajoite | Vaihe |
|---|---|
| Policy routing | 37 |
| Multipath-reitityksen laajempi tuki | 37 |
| getent-tulosten tulkinnan epävarmuus | 16 |
| Socket-osoitteiden luokittelu | 16 |
| Palvelukutsujen keskeyttäminen | 24 |
| Python 3.11-, 3.13- ja 3.14-yhteensopivuustestaus | CI (vaihe 13.2); Docker-testaus erikseen |

Nykyistä diagnostiikkalogiikkaa ei ole muutettu näiden tulevien tehtävien vuoksi.
