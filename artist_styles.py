"""Artist → style resolver (local, "type beat" UX).

Producers describe genres by artist ("nessa barrett type beat", «как у дуа
липы»), not by textbook genre names. This module maps well-known artist names
(EN + RU transliterations, prefix-matched so Russian case endings work) onto
PLG's genre profiles + a short production *vibe* line.

Legal hygiene: artist names NEVER reach the LLM or the generated content.
``sanitize_artist_names`` replaces each detected name with its vibe descriptor
before the prompt goes to the gateway; resolution itself is purely local.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# stems are lowercase; matched with \b<stem> so RU inflections ("бибера") hit.
# vibe = production language only — no artist/label names, safe for the LLM.
_S = dict

ARTIST_STYLES: tuple[dict[str, Any], ...] = (
    # --- Pop / dance-pop ---
    _S(stems=("dua lipa", "дуа лип"), genre="disco_pop",
       vibe="glossy nu-disco pop: four-on-floor, rubbery funk bassline, bright retro synth stabs"),
    _S(stems=("ariana grande", "ариана гранд"), genre="pop",
       vibe="polished vocal-first pop-R&B: airy pads, tight 808-tinged drums, big chorus lift"),
    _S(stems=("justin bieber", "джастин бибер", "бибер"), genre="pop",
       vibe="smooth radio pop-R&B: warm keys, clean 808 pulse, laid-back top-line space"),
    _S(stems=("taylor swift", "тейлор свифт"), genre="pop",
       vibe="anthemic songwriter pop: bright synth strums, steady backbeat, chorus explosion"),
    _S(stems=("olivia rodrigo", "оливия родриго"), genre="pop",
       vibe="raw gen-z pop with pop-punk edges: driving drums, gritty guitar-like synth energy"),
    _S(stems=("sabrina carpenter", "сабрина карпентер"), genre="pop",
       vibe="playful glossy pop: bouncy bassline, funky guitar-ish chops, cheeky hooks"),
    _S(stems=("harry styles", "гарри стайлс"), genre="pop",
       vibe="sunny retro pop-rock: warm analog keys, live-feel backbeat, feel-good hook"),
    _S(stems=("miley cyrus", "майли сайрус"), genre="pop",
       vibe="stadium pop with rock grit: punchy backbeat, saturated synth-guitar energy"),
    # --- Dark pop / synth ---
    _S(stems=("the weeknd", "weeknd", "викенд", "уикенд"), genre="dark_pop",
       vibe="dark 80s synth-pop: pulsing analog bass, gated drums, neon minor-key melancholia"),
    _S(stems=("nessa barrett", "несса баррет"), genre="dark_pop",
       vibe="moody alt-pop: haunted pads, sparse heavy drums, intimate dark vocal space"),
    _S(stems=("billie eilish", "билли айлиш"), genre="dark_pop",
       vibe="whisper-close dark pop: sub-heavy minimal beat, eerie detuned textures, huge space"),
    _S(stems=("lana del rey", "лана дель рей"), genre="dark_pop",
       vibe="cinematic sadcore pop: slow lush chords, vintage reverb haze, trip-hop tinged drums"),
    # --- Pop-rap ---
    _S(stems=("nicki minaj", "ники минаж"), genre="pop_rap",
       vibe="flamboyant pop-rap: bouncy club 808s, chant-ready hook space, playful synth stabs"),
    _S(stems=("doja cat", "доджа кэт", "доджа"), genre="pop_rap",
       vibe="slinky funk-tinged pop-rap: groovy bassline, minimal crisp drums, catchy talk-sing hook"),
    _S(stems=("cardi b", "карди би"), genre="pop_rap",
       vibe="hard club pop-rap: knocking 808s, sparse piano/brass stabs, big chant energy"),
    _S(stems=("megan thee stallion", "меган зе сталлион"), genre="pop_rap",
       vibe="bouncy southern pop-rap: swinging hats, aggressive 808 knock, confident stab hooks"),
    # --- Trap / melodic rap ---
    _S(stems=("travis scott", "трэвис скотт", "травис скотт"), genre="trap",
       vibe="dark psychedelic trap: cavernous 808s, eerie sparse synths, wide autotune ad-lib space"),
    _S(stems=("don toliver", "toliver", "толивер"), genre="toliver",
       vibe="floating psychedelic melodic trap: glide pads that rise and hover, bell-harp hooks, warm acoustic-bass foundation under the 808, soft drums far in the back, reverb tails on everything"),
    _S(stems=("kanye", "kanye west", "ye ", "канье", "канье уэст"), genre="kanye",
       vibe="soul/gospel sample-flip hip-hop: chopped vintage vinyl, warm chipmunk-soul vocal chops, lush dusty chord stacks, tape echo, live-feel swung drums, brass stabs"),
    _S(stems=("drake", "дрейк"), genre="trap",
       vibe="moody melodic rap: ambient night pads, smooth rolling 808s, understated hook"),
    _S(stems=("future", "фьючер"), genre="trap",
       vibe="hazy street trap: woozy bell/flute loops, sliding 808s, hypnotic pocket"),
    _S(stems=("chief keef", "чиф киф"), genre="trap",
       vibe="dark Chicago trap: epic half-time synth pads, frequent brass stabs, "
            "hook/chorus hits with the full arrangement, verse strips down"),
    _S(stems=("metro boomin", "метро бумин"), genre="trap",
       vibe="cinematic dark trap: orchestral stabs, booming 808s, villain-theme atmosphere"),
    _S(stems=("21 savage", "21 саваж"), genre="trap",
       vibe="cold minimal trap: icy keys, deadpan pocket, knocking sparse 808s"),
    _S(stems=("young thug", "янг таг"), genre="trap",
       vibe="elastic melodic trap: playful synth leads, bouncy 808s, off-kilter energy"),
    _S(stems=("lil baby", "лил бейби"), genre="trap",
       vibe="urgent melodic trap: piano/pluck loops, driving hats, rolling 808 runs"),
    _S(stems=("gunna", "ганна"), genre="trap",
       vibe="slick melodic trap: watery guitar/pluck loops, lazy pocket, smooth 808 glides"),
    _S(stems=("juice wrld", "джус ворлд"), genre="trap",
       vibe="melodic emo trap: electric guitar lead (sad, hype), deep boomy 808 low end, minimal drums, sing-rap hook space"),
    _S(stems=("kanye", "канье", "kanye west"), genre="trap",
       vibe="soulful maximalist rap: chopped soul textures, marching drums, gospel-tinged chords"),
    # --- Opium / rage ---
    _S(stems=("playboi carti", "карти"), genre="opium",
       vibe="opium rage minimalism: phrygian bell/pluck hook, vast empty space, distorted 808 pulse"),
    _S(stems=("ken carson", "кен карсон"), genre="rage",
       vibe="glitchy rage trap: sawy stab hooks, pounding off-beat 808s, reckless energy"),
    _S(stems=("destroy lonely", "дестрой лонли"), genre="opium",
       vibe="hazy opium plugg: weird unique synth layers, smeared dark synths, lazy pocket, hypnotic loop"),
    _S(stems=("nettspend", "неттспенд"), genre="underground",
       vibe="early life crisis underground: playful natural minor melodies climbing upward, "
            "150 BPM (or 70-90 half-time same energy), heavy-hitting 808"),
    _S(stems=("lil uzi vert", "узи верт", "lil uzi"), genre="rage",
       vibe="hyper melodic rage: video-game synth leads over a simple sound "
            "palette but genuinely complex, busy drum programming — the drums "
            "carry the record, not the melody; explosive 808 bounce"),
    _S(stems=("yeat", "йит"), genre="rage",
       vibe="twizzy rage: detuned bell hooks, turbo 808 pulse, chaotic ad-lib space"),
    _S(stems=("xxxtentacion", "тентасьон"), genre="rage",
       vibe="raw emo trap: lo-fi distorted 808s, aggressive minimal drums, dark confessional space"),
    # --- Drill ---
    _S(stems=("pop smoke", "поп смоук"), genre="drill",
       vibe="brooklyn drill: sliding 808s, ominous string/choir loop, aggressive bounce"),
    _S(stems=("central cee", "сентрал си"), genre="drill",
       vibe="uk drill: shadowy piano motif, ghost-kick shuffle, gliding 808 slides"),
    _S(stems=("ice spice", "айс спайс"), genre="drill",
       vibe="bronx drill-pop: playful bells, sliding 808s, chant-ready minimal hook"),
    # --- R&B ---
    _S(stems=("sza", "сза"), genre="rnb",
       vibe="dreamy alt-R&B: floating dorian keys, lazy pocket, warm round bass"),
    _S(stems=("frank ocean", "фрэнк оушен"), genre="rnb",
       vibe="introspective art-R&B: sparse keys, ambient space, soft asymmetric drums"),
    _S(stems=("brent faiyaz", "брент файяз"), genre="rnb",
       vibe="woozy toxic R&B: detuned keys, behind-the-beat drums, dark late-night mood"),
    _S(stems=("summer walker", "саммер уокер"), genre="rnb",
       vibe="silky bedroom R&B: guitar-tinged keys, soft knock, intimate space"),
    # --- K-pop ---
    _S(stems=("blackpink", "блэкпинк"), genre="kpop",
       vibe="explosive girl-crush k-pop: brassy synth stabs, trap-tinged drops, chant hooks"),
    _S(stems=("bts", "бтс"), genre="kpop",
       vibe="polished k-pop: layered bright synths, punchy four-on-floor chorus, dynamic sections"),
    _S(stems=("newjeans", "ньюджинс"), genre="kpop",
       vibe="y2k k-pop: jersey-tinged bounce, airy pads, cute minimal hooks"),
    # --- EDM / dance ---
    _S(stems=("martin garrix", "мартин гаррикс", "гаррикс"), genre="edm",
       vibe="festival big-room: massive supersaw drops, snare-riser builds, euphoric anthem lead"),
    _S(stems=("david guetta", "дэвид гетта", "гетта"), genre="edm",
       vibe="radio dance anthem: four-on-floor, sidechained synth chords, vocal-drop energy"),
    _S(stems=("avicii", "авичи"), genre="edm",
       vibe="melodic festival house: folky lead hooks, piano builds, uplifting drops"),
    _S(stems=("alan walker", "алан уокер"), genre="edm",
       vibe="melancholic electro: plucky minor leads, gated pads, clean modern drop"),
    _S(stems=("marshmello", "маршмелло"), genre="edm",
       vibe="future-bounce pop EDM: bright plucks, happy chord drops, simple singable lead"),
    _S(stems=("calvin harris", "кэлвин харрис"), genre="house",
       vibe="funky dance-pop house: disco bassline, four-on-floor, glossy retro synths"),
    _S(stems=("fred again", "фред эгейн"), genre="house",
       vibe="emotional uk house: chopped vocal textures, warm garage-tinged bounce, intimate drops"),
    _S(stems=("kaytranada", "кейтранада"), genre="house",
       vibe="swung neo-house: syncopated bass groove, dusty warm chords, head-nod bounce"),
    _S(stems=("peggy gou", "пегги гу"), genre="house",
       vibe="groovy 90s-tinged house: rolling bassline, retro stabs, hypnotic vocal chops"),
    _S(stems=("fisher", "фишер"), genre="house",
       vibe="cheeky tech-house: fat rolling bass, minimal percussion, one huge hook"),
    _S(stems=("charlotte de witte", "шарлотта де витт"), genre="techno",
       vibe="dark peak-time techno: relentless kick, acid stabs, tunnel atmosphere"),
    _S(stems=("skrillex", "скриллекс"), genre="dubstep",
       vibe="surgical bass music: talking growls, half-time lurch, explosive fills"),
    _S(stems=("pendulum", "пендулум"), genre="dnb",
       vibe="arena drum & bass: rock-energy breaks, huge reese bass, anthemic leads"),
    _S(stems=("charli xcx", "чарли иксисиэкс"), genre="hyperpop",
       vibe="club-brat hyperpop: rubbery bass stabs, crunchy claps, ironic detached hooks"),
    _S(stems=("100 gecs", "100 гекс"), genre="hyperpop",
       vibe="maximalist glitch-pop: pitch-bent leads, blown-out drums, chaotic sugar-rush"),
    # --- Latin / global ---
    _S(stems=("bad bunny", "бэд банни"), genre="reggaeton",
       vibe="modern reggaeton: dembow bounce, moody synth plucks, big vocal space"),
    _S(stems=("j balvin", "джей балвин"), genre="reggaeton",
       vibe="glossy reggaeton: clean dembow, tropical plucks, radio-ready hooks"),
    _S(stems=("daddy yankee", "дэдди янки"), genre="reggaeton",
       vibe="classic reggaeton: hard dembow drums, brassy stabs, party chant energy"),
    _S(stems=("karol g", "кароль джи"), genre="reggaeton",
       vibe="melodic reggaeton-pop: warm plucks, smooth dembow, sing-along hooks"),
    _S(stems=("burna boy", "бурна бой"), genre="afrobeats",
       vibe="afro-fusion: laid-back tresillo groove, warm log bass, horn-tinged hooks"),
    _S(stems=("wizkid", "визкид"), genre="afrobeats",
       vibe="smooth afrobeats: silky shaker groove, mellow plucks, late-night warmth"),
    _S(stems=("rema", "рема"), genre="afrobeats",
       vibe="afro-rave: energetic log drums, hypnotic chant melodies, bouncing percussion"),
    _S(stems=("tyla", "тайла"), genre="amapiano",
       vibe="pop-amapiano: rolling log drum bass, soft piano chords, sultry groove"),
    _S(stems=("sean paul", "шон пол"), genre="dancehall",
       vibe="classic dancehall: 3+3+2 riddim, punchy rims, party chant space"),
    # --- Funk / soul ---
    _S(stems=("bruno mars", "бруно марс"), genre="funk",
       vibe="retro funk-pop: slap bass pocket, tight horns-style stabs, feel-good swagger"),
    _S(stems=("anderson .paak", "anderson paak", "андерсон пак"), genre="funk",
       vibe="live-feel neo-funk: greasy drum pocket, warm keys, talk-sing swagger"),
    _S(stems=("silk sonic", "силк соник"), genre="soul",
       vibe="70s silk soul: lush strings-feel pads, warm round bass, slow-jam backbeat"),
    _S(stems=("adele", "адель"), genre="soul",
       vibe="torch-song soul-pop: grand piano chords, patient backbeat, huge emotional swells"),
    _S(stems=("amy winehouse", "эми уайнхаус"), genre="soul",
       vibe="retro motown soul: swinging backbeat, baritone-warm bass, smoky keys"),
    # --- West Coast ---
    _S(stems=("snoop dogg", "снуп дог"), genre="west_coast",
       vibe="g-funk bounce: whiny lead synth, deep funk bass, laid-back west-coast pocket"),
    # --- Hip-hop classic ---
    _S(stems=("j dilla", "джей дилла", "дилла"), genre="boombap",
       vibe="drunken-swing boom bap: off-grid MPC pocket, warm chopped keys, dusty bass"),
    _S(stems=("mf doom", "мф дум"), genre="boombap",
       vibe="crate-digger boom bap: obscure loop chops, lazy heavy backbeat, vinyl grit"),
    _S(stems=("nujabes", "нуджабес"), genre="lofi",
       vibe="jazzy lo-fi hip-hop: airy piano loops, soft swung drums, wistful warmth"),
    _S(stems=("eminem", "эминем"), genre="boombap",
       vibe="aggressive 2000s rap: dark piano/strings loop, hard snapping backbeat, double-time space"),
    # --- Rock / cinematic ---
    _S(stems=("nirvana", "нирвана"), genre="rock",
       vibe="grunge power: quiet-loud dynamics, raw power-chord riffs, pounding backbeat"),
    _S(stems=("metallica", "металлика"), genre="rock",
       vibe="metal drive: palm-muted riff engine, double-kick energy, dark minor aggression"),
    _S(stems=("linkin park", "линкин парк"), genre="rock",
       vibe="nu-metal hybrid: heavy riffs over electronic textures, anthemic angst chorus"),
    _S(stems=("arctic monkeys", "арктик манкис"), genre="rock",
       vibe="indie swagger rock: slinky bass riffs, tight dry drums, night-drive cool"),
    _S(stems=("hans zimmer", "ханс циммер", "циммер"), genre="cinematic",
       vibe="modern epic score: braams, string ostinato engine, colossal half-time hits"),
    _S(stems=("ludwig goransson", "ludwig göransson", "йоранссон"), genre="cinematic",
       vibe="hybrid orchestral: pulsing synth bass under orchestra, bold brass themes"),
)


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[Any, dict[str, Any]], ...]:
    # Exact word match + up to 2 Cyrillic tail chars so Russian case endings
    # hit ("дуа липы", "бибера", "гарриксом") without eating unrelated words.
    out: list[tuple[Any, dict[str, Any]]] = []
    for entry in ARTIST_STYLES:
        for stem in entry["stems"]:
            pattern = re.compile(rf"\b{re.escape(stem.strip())}[а-яё]{{0,2}}\b", re.IGNORECASE)
            out.append((pattern, entry))
    return tuple(out)


def resolve_artist_style(text: str) -> dict[str, Any] | None:
    """First artist-style hit in the text, or None. Purely local."""
    low = (text or "").lower()
    if not low:
        return None
    for pattern, entry in _compiled():
        if pattern.search(low):
            return entry
    return None


def sanitize_artist_names(text: str) -> str:
    """Replace artist names with their vibe descriptor before any LLM call.

    Keeps the musical intent ("dark 80s synth-pop…") while keeping artist and
    label names out of prompts and generated content.
    """
    if not text:
        return text
    out = text
    for pattern, entry in _compiled():
        if pattern.search(out):
            out = pattern.sub(f"({entry['vibe']})", out, count=1)
            out = pattern.sub("", out)
    return out
