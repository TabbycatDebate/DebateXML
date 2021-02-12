#!/usr/bin/python3

from datetime import datetime
from itertools import groupby
import sys
from xml.etree import ElementTree as ET


def _handle_datetime(in_str, date=True, time=False):
	dt_format = ""
	dt_str = ""
	cal = in_str.split(" ") # [Date, Time, AM/PM]

	if date:
		dt_format += "%m/%d/%Y"
		d = cal[0].split("/") # [Day, Month, Year]
		for c in d[:1]:
			c.zfill(2)
		dt_str += "/".join(d)
	if date and time:
		dt_format += " "
		dt_str += " "
	if time:
		dt_format += "%H:%M:%S %p"
		t = cal[1].split(":") # [Hour, Minutes(, Seconds)]
		if len(t) == 2:
			t.append("00") # Add seconds if not there
		t[0].zfill(2)
		dt_str += ":".join(t) + " " + cal[2]

	return datetime.strptime(dt_str, dt_format)


class TabroomImporter:

	STYLES = {
		'Policy': None,
		'Parli': None,
		'Lincoln-Douglas': 'ld',
		'WUDC': 'wudc',
		'Public Forum': None,
		'Other': None
	}

	ID_PREFIX = {
		'JUDGE': 'A',
		'DEBATE': 'D',
		'EVENT': 'E',
		'SCHOOL': 'I',
		'STUDENT': 'S',
		'ENTRY': 'T',
		'ROOM': 'V'
	}

	BALLOT_TYPE_IDS = {
		'Ballot': None,
		'Speaker Points': None,
		'Team Points': None,
		'Speaker Rank': None,
		'Team Ranks': None
	}

	BALLOT_TYPE_ATTR = {
		'Ballot': 'rank',
		'Speaker Points': None,
		'Team Points': None,
		'Speaker Rank': 'rank',
		'Team Ranks': 'rank'
	}

	# People/teams without an institution have a school value of -1, which
	# would have also been added to institutions. Removing for clarity.
	UNAFFILATED_ID = "-1"

	# For objects which do not exist, such as an opposing team to a bye
	UNEXISTANT_ID = "-99"

	def __init__(self, in_xml):
		self.tr = in_xml
		self.dml = ET.Element('tournament')
		self.get_score_format()

	def get_score_format(self):
		# <ROUNDRESULT seems to be deprecated, and may not have ID
		self.has_roundresult = self.tr.find("ROUNDRESULT") is not None
		self.has_rounds = self.tr.find('ROUND') is not None

		if self.has_rounds:
			for score in self.tr.findall('SCORES'):
				self.BALLOT_TYPE_IDS[score.find('SCORE_NAME').text] = score.find('ID').text
			self.BALLOT_TYPE_NAMES = {v: k for k, v in self.BALLOT_TYPE_IDS.items()}

		self.is_bp = False # Is set to True in self.parse_tourn(), assuming it is so for all divisions

	def run(self):
		self.parse_tourn()
		self.parse_rounds()
		self.parse_participants()
		self.parse_schools()
		self.parse_rooms()
		self.parse_events()

		return self.dml

	def parse_tourn(self):
		tr_tourn = self.tr.find('TOURN')

		self.dml.set('name', tr_tourn.get('TOURNNAME', 'Tournament'))
		if tr_tourn.find('STARTDATE') is not None:
			self.dml.set('start-date', _handle_datetime(tr_tourn.find('STARTDATE').text).strftime("%Y-%m-%d"))
		if tr_tourn.find('ENDDATE') is not None:
			self.dml.set('end-date', _handle_datetime(tr_tourn.find('ENDDATE').text).strftime("%Y-%m-%d"))

		# Whether to have styles under divisions or the tournament
		self.num_events = len(self.tr.findall('EVENT'))
		if len(self.tr.findall('EVENT[STYLE]')) != 0:
			tr_events = self.tr.findall('EVENT')
			event_style = tr_events[0].find('STYLE')
			same_format = len(tr_events) == 1 or all(e.find('STYLE') == event_style for e in tr_events)
			if self.STYLES[event_style] is not None and same_format:
				if event_style == 'WUDC':
					self.is_bp = True
				self.dml.set('style', self.STYLES[event_style])

	def parse_rounds(self):
		if self.has_rounds:
			self.parse_rounds_by_round()
		else: # Uses only <ROUNDRESULT
			self.parse_rounds_by_roundresult()

	def parse_rounds_by_round(self):
		for tr_round in self.tr.findall('ROUND'):
			round = ET.SubElement(self.dml, 'round', {
				'name': tr_round.find('LABEL').text,
				'elimination': str(int(tr_round.find('RD_NAME').text) > 9).lower()
			})

			if self.num_events > 1 and tr_round.find('EVENT') is not None:
				round.set('division', self.ID_PREFIX['EVENT'] + tr_round.find('EVENT').text)
			timeslot = tr_round.find('TIMESLOT')
			if timeslot is not None and self.tr.find("TIMESLOT[ID='%s']" % timeslot.text) is not None:
				round.set('start', _handle_datetime(
					self.tr.find("TIMESLOT[ID='%s']/START" % timeslot.text).text, time=True).isoformat()
				)

			for panel in self.tr.findall("PANEL[ROUND='%s']" % tr_round.find('ID').text):
				panel_id = panel.find('ID').text
				entries = [e.text for e in self.tr.findall("BALLOT[PANEL='%s']/ENTRY" % panel_id)]
				if (panel.find('BYE') is None or panel.find('BYE').text == "0") and self.UNEXISTANT_ID not in entries:
					self.interpret_debate(round, panel)
				else:
					byes = set(entries)
					for bye in byes:
						if bye != self.UNEXISTANT_ID:
							# ID included just for debugging purposes
							bye_tag = ET.SubElement(round, 'bye')
							bye_tag.text = self.ID_PREFIX['ENTRY'] + bye

	def interpret_debate(self, round, panel):
		ballot_adjs = set() # Chairs don't seem to be defined
		for adj in self.tr.findall("BALLOT[PANEL='%s']/JUDGE" % panel.find('ID').text):
			ballot_adjs.add(self.ID_PREFIX['JUDGE'] + adj.text)

		debate = ET.SubElement(round, 'debate', {
			'id': self.ID_PREFIX['DEBATE'] + panel.find('ID').text,
		})
		if self.UNEXISTANT_ID not in ballot_adjs:
			debate.set('adjudicators', " ".join(ballot_adjs))
		if panel.find('ROOM').text != self.UNEXISTANT_ID:
			debate.set('venue', self.ID_PREFIX['ROOM'] + panel.find('ROOM').text)

		entries_added = set()
		for tr_ballot_side in sorted(
			self.tr.findall("BALLOT[PANEL='%s']" % panel.find('ID').text),
			key=lambda e: int(e.find('SIDE').text)
		):
			entry = tr_ballot_side.find('ENTRY').text
			entry_id = self.ID_PREFIX['ENTRY'] + entry
			if entry_id in entries_added:
				continue
			side = ET.SubElement(debate, 'side', {
				'team': entry_id
			})
			entries_added.add(entry_id)
			self.interpret_side(side, panel.find('ID').text, entry)

	def interpret_side(self, side, panel_id, entry_id):
		tr_ballots = self.tr.findall("BALLOT[PANEL='%s'][ENTRY='%s']" % (panel_id, entry_id))
		speaker_scores = {}
		for tr_ballot in tr_ballots:
			b_id = tr_ballot.find('ID').text
			ballot_team_rank = self.tr.find("BALLOT_SCORE[BALLOT='%s'][SCORE_ID='%s']" % (b_id, self.BALLOT_TYPE_IDS['Ballot']))
			ballot_team_score_total = sum(float(e.find('SCORE').text) for e in 
				self.tr.findall("BALLOT_SCORE[BALLOT='%s'][SCORE_ID='%s']" % (b_id, self.BALLOT_TYPE_IDS['Speaker Points']))
			)

			ballot = ET.SubElement(side, 'ballot')
			adj_id = ""
			if tr_ballot.find('JUDGE').text != self.UNEXISTANT_ID:
				adj_id = self.ID_PREFIX['JUDGE'] + tr_ballot.find('JUDGE').text
				ballot.set('adjudicators', adj_id)
			if ballot_team_rank is not None:
				max_rank = 4 if self.is_bp else 2
				rank = max_rank - int(ballot_team_rank.find('SCORE').text)
				ballot.set('rank', str(rank))
			if ballot_team_score_total > 0:
				ballot.text = str(round(ballot_team_score_total, 1))

			for ss in self.tr.findall("BALLOT_SCORE[BALLOT='%s']" % b_id):
				rec = ss.find('RECIPIENT').text
				if rec == entry_id:
					continue
				if rec not in speaker_scores:
					speaker_scores[rec] = {}
				if adj_id not in speaker_scores[rec]:
					speaker_scores[rec][adj_id] = {}
				speaker_scores[rec][adj_id][ss.find('SCORE_ID').text] = ss.find('SCORE').text

		for speaker, adj_s in speaker_scores.items():
			speech = ET.SubElement(side, 'speech', {'speaker': self.ID_PREFIX['STUDENT'] + speaker})
			for adj, ss in adj_s.items():
				ballot = ET.SubElement(speech, 'ballot')
				if adj is not '':
					ballot.set('adjudicators', adj)
				for t, s in ss.items():
					attr = self.BALLOT_TYPE_ATTR[self.BALLOT_TYPE_NAMES[t]]
					if attr is None:
						ballot.text = s
					else:
						ballot.set(attr, s)

	def parse_rounds_by_roundresult(self):
		for round_result in self.tr.findall('ROUNDRESULT'):
			round = ET.SubElement(self.dml, 'round', {
				'name': round_result.get('RoundName'),
				'elimination': str(round_result.get('RoundType') == 'Elim').lower()
			})
			if self.num_events > 1:
				round.set('division', self.ID_PREFIX['EVENT'] + round_result.get('EventID'))

			for panel_id, tr_ballots_gen in groupby(round_result.findall('BALLOT'), key=lambda l: l.get('Panel')):
				tr_ballots = list(tr_ballots_gen)

				if tr_ballots[0].get('JudgeID') == self.UNEXISTANT_ID:
					for team in tr_ballots[0].findall("SCORE[@ScoreFor='Team']"):
						if team.get('Recipient') == self.UNEXISTANT_ID:
							continue
						bye = ET.SubElement(round, 'bye')
						bye.text = self.ID_PREFIX['ENTRY'] + team.get('Recipient')
					break

				debate = ET.SubElement(round, 'debate', {
					'id': self.ID_PREFIX['DEBATE'] + panel_id,
					'adjudicators': " ".join([
						self.ID_PREFIX['JUDGE'] + b.get('JudgeID') for b in tr_ballots if b.get('JudgeID') is not None
					])
				})
				if tr_ballots[0].get('RoomID') is not None:
					debate.set('venue', self.ID_PREFIX['ROOM'] + tr_ballots[0].get('RoomID'))

				# Get sides
				sides = {}
				for tr_score in sorted(tr_ballots[0].findall("SCORE[@ScoreFor='Team']"), key=lambda l: int(l.get('Side', 0))):
					side = ET.SubElement(debate, 'side', {'team': self.ID_PREFIX['ENTRY'] + tr_score.get('Recipient')})
					sides[tr_score.get('Recipient')] = side

				# Get speakers if available
				speakers = {}
				cur_team = ""
				for tr_score in tr_ballots[0].findall("SCORE"):
					if tr_score.get('ScoreFor') == 'Team':
						cur_team = tr_score.get('Recipient')
					elif tr_score.get('Recipient') not in speakers:
						speaker = ET.SubElement(sides[cur_team], 'speech', {
							'speaker': self.ID_PREFIX['STUDENT'] + tr_score.get('Recipient')
						})
						speakers[tr_score.get('Recipient')] = speaker

				for tr_ballot in tr_ballots:
					adj_id = tr_ballot.get('JudgeID')

					for p_type in [sides, speakers]:
						for p_id, tag in p_type.items():
							adj_decision = {}
							for tr_score in tr_ballot.findall("SCORE[@Recipient='%s']" % p_id):
								adj_decision[tr_score.get('SCORE_NAME')] = tr_score.text
							ballot = ET.SubElement(tag, 'ballot', {
								'adjudicators': self.ID_PREFIX['JUDGE'] + adj_id
							})
							for score_type, value in adj_decision.items():
								attr = self.BALLOT_TYPE_ATTR[score_type]
								if score_type == 'Ballot':
									max_rank = len(sides.keys())
									ballot.set('rank', str(max_rank - int(value)))
								elif attr is not None:
									ballot.set(attr, value)
								else:
									ballot.text = value

	def parse_participants(self):
		participants = ET.SubElement(self.dml, 'participants')

		self.parse_entries(participants)
		self.parse_judges(participants)

	def parse_entries(self, participants):
		for entry in self.tr.findall('ENTRY'):
			team = ET.SubElement(participants, 'team', {
				'id': self.ID_PREFIX['ENTRY'] + entry.find('ID').text
			})

			# Names are optional apparently
			if entry.find('FULLNAME') is not None:
				team.set('name', entry.find('FULLNAME').text)
			elif entry.find('CODE') is not None:
				team.set('name', entry.find('CODE').text)
			else:
				team.set('name', 'UNKNOWN')

			if entry.find('CODE') is not None:
				team.set('code', entry.find('CODE').text)

			if self.num_events > 1:
				team.set('division', self.ID_PREFIX['EVENT'] + entry.find('EVENT').text)

			# Add speakers
			inst = ""
			if entry.find('SCHOOL') is not None and entry.find('SCHOOL') != self.UNAFFILATED_ID:
				inst = self.ID_PREFIX['SCHOOL'] + entry.find('SCHOOL').text

			for student in self.tr.findall("ENTRY_STUDENT[ENTRY='%s']" % entry.find('ID').text):
				speaker = ET.SubElement(team, 'speaker', {
					'id': self.ID_PREFIX['STUDENT'] + student.find('ID').text
				})
				speaker.text = student.find('FIRST').text + " " + student.find('LAST').text

				if student.find('SCHOOL') is not None and student.find('SCHOOL') != self.UNAFFILATED_ID:
					other_inst = self.ID_PREFIX['SCHOOL'] + student.find('SCHOOL').text
					if inst == "":
						speaker.set('institutions', other_inst)
					elif other_inst != inst:
						speaker.set('institutions', inst + " " + other_inst)
					else:
						speaker.set('institutions', inst)
				elif inst != "":
					speaker.set('institutions', inst)

	def parse_judges(self, participants):
		for judge in self.tr.findall('JUDGE'):
			adj = ET.SubElement(participants, 'adjudicator', {
				'id': self.ID_PREFIX['JUDGE'] + judge.find('ID').text
			})
			if judge.find('TABRATING') is not None and judge.find('TABRATING').text is not None:
				adj.set('score', judge.find('TABRATING').text)
			if judge.find('SCHOOL') is not None and judge.find('SCHOOL').text != self.UNAFFILATED_ID:
				adj.set('institutions', self.ID_PREFIX['SCHOOL'] + judge.find('SCHOOL').text)
			adj.text = judge.find('FIRST').text + " " + judge.find('LAST').text

	def parse_schools(self):
		for school in self.tr.findall('SCHOOL'):
			if school.find('ID').text == self.UNAFFILATED_ID:
				continue
			institution = ET.SubElement(self.dml, 'institution', {
				'id': self.ID_PREFIX['SCHOOL'] + school.find('ID').text
			})
			if school.find('CODE') is not None and school.find('CODE').text is not None:
				institution.set('reference', school.find('CODE').text)
			if school.find('REGION') is not None and school.find('REGION').text is not None:
				institution.set('region', school.find('REGION').text)
			institution.text = school.find('SCHOOLNAME').text

	def parse_rooms(self):
		for room in self.tr.findall('ROOM'):
			venue = ET.SubElement(self.dml, 'venue', {
				'id': self.ID_PREFIX['ROOM'] + room.find('ID').text,
				'score': room.find('QUALITY').text
			})
			venue.text = room.find('ROOMNAME').text

	def parse_events(self):
		if self.num_events == 1:
			return
		for event in self.tr.findall('EVENT'):
			division = ET.SubElement(self.dml, 'division', {
				'id': self.ID_PREFIX['EVENT'] + event.find('ID').text
			})
			division.text = event.find('EVENTNAME').text


if __name__ == "__main__":
	input_xml = ET.fromstring(sys.stdin.read())
	tc = TabroomImporter(input_xml)
	output_xml = ET.tostring(tc.run())
	sys.stdout.buffer.write(output_xml)
	sys.stdout.write("\n") # XML doesn't add newline
