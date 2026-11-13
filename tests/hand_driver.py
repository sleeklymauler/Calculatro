"""The mocked game's hand, deck and Sort buttons, moved frame by frame, for the face-down hint
and the observations the mod keeps of the hand's sorts."""

from panel_driver import TEXTS

SETUP = r"""
function make_card(id, suit, facing, center)
  return {base = {id = id, value = tostring(id), suit = suit}, config = {center_key = center or 'c_base'},
          ability = {}, facing = facing or 'front', states = {hover = {}, drag = {}}}
end
function make_buttons()
  RANK_BUTTON = {UIT = 'C', config = {button = 'sort_hand_value'}}
  SUIT_BUTTON = {UIT = 'C', config = {button = 'sort_hand_suit'}}
  return {UIRoot = {UIT = 'ROOT', config = {}, children = {RANK_BUTTON, SUIT_BUTTON}}}
end
G.buttons = make_buttons()
G.hand.cards, G.deck.cards, G.discard.cards, G.jokers.cards = {}, {}, {}, {}
G.hand.config.sort = 'desc'
CLICKS = 0
function set_cards(area, list)
  area.cards = {}
  for i, c in ipairs(list) do area.cards[i] = c end
end
function press(button)  -- as UIElement:click does, just before the sort runs
  CLICKS = CLICKS + 1
  button.last_clicked = CLICKS
end
"""


class Game:
    """The mocked game's hand and deck, moved frame by frame."""

    def __init__(self, runtime):
        self.rt, self.mod = runtime
        self.rt.execute(SETUP)
        self.mod.settings.face_down = True  # off by default
        self.make = self.rt.eval("make_card")
        self.mod.track_hand()

    def card(self, text, facing="front"):
        """A card from text: '9H', 'TS', 'Stone'."""
        if text == "Stone":
            return self.make(5, "Clubs", facing, "m_stone")
        rank = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}.get(text[0]) or int(text[0])
        suit = {"S": "Spades", "H": "Hearts", "C": "Clubs", "D": "Diamonds"}[text[1]]
        return self.make(rank, suit, facing)

    def set_area(self, area, cards):
        self.rt.eval("set_cards")(self.rt.eval(f"G.{area}"), self.rt.table_from(cards))

    def frame(self, n=1):
        for _ in range(n):
            self.mod.track_hand()
            self.mod.refresh_hint()

    def sort(self, mode, cards):
        """A Sort button pressed: the hand's in this order next frame, then settles."""
        self.rt.eval("G.hand.config").sort = mode
        self.set_area("hand", cards)
        self.rt.eval("press")(self.rt.eval("RANK_BUTTON" if mode == "desc" else "SUIT_BUTTON"))
        self.frame(2)

    def deal(self, hand, deck, mode="desc"):
        """The hand drawn from the deck, which sorts it, into this order."""
        self.set_area("deck", list(hand) + list(deck))
        self.frame()
        self.rt.eval("G.hand.config").sort = mode
        self.set_area("deck", deck)
        self.set_area("hand", hand)
        self.frame(2)

    def hover(self, card, on=True):
        card.states.hover["is"] = on or None
        self.frame()

    def hint(self):
        box = self.mod.hint_box
        if box is None:
            return None
        return "|".join(self.rt.execute(TEXTS)(box.definition).values())

    def sorts(self) -> list:
        """The observations kept, in the order recorded."""
        return list(self.mod.observations.values())

    def latest(self, mode):
        """The latest observation of `mode` kept, or None."""
        return next((o for o in reversed(self.sorts()) if o.mode == mode), None)

    def possible(self, card):
        found = self.mod.possible_cards(card)
        if found is None:
            return None
        return {"Stone" if v.stone else (v.rank_id, v.suit) for v in found.values()}
