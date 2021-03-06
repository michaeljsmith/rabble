#!/usr/bin/python

import threading
import sys
import time
import os
import subprocess
import errno
import re
import copy
import random

class ExplodeError(Exception): pass

explode_arg_re = re.compile(r'"(?:.*\\")*.*"|[a-zA-Z0-9_]+')
def explode_args(string):
	arg_re = explode_arg_re
	args = []
	while True:
		string = string.lstrip()
		if not string:
			return args
		m = arg_re.match(string)
		if not m:
			raise ExplodeError()
		args.append(string[m.start():m.end()].strip('"'))
		string = string[m.end():]

class ServerChannelMessage(object):
	def __init__(self, id, command, args):
		self.id = id
		self.command = command
		self.args = args

class ThreadMessageQueue(object):
	def __init__(self):
		self.items = []
		self.lock = threading.Lock()

	def append(self, message):
		self.lock.acquire()
		try:
			self.items.append(message)
		finally:
			self.lock.release()

	def pop(self):
		self.lock.acquire()
		message = None
		try:
			message = self.items.pop(0)
		finally:
			self.lock.release()
		return message

class ServerChannelMessageQueue(ThreadMessageQueue):
	pass

class ServerChannelIO(object):
	def __init__(self):
		pass

class ServerChannel(object):
	def __init__(self, id, io, agent, master_channel=False):
		self.id = id
		self.io = io
		self.agent = agent
		self.master_channel = master_channel

	def send_message(self, message):
		self.io.send_message(message)

	def listen(self, on_message, on_finished):
		try:
			while not self.io.is_end():
				message = self.io.read_message()
				if message:
					args = None
					error_message = None
					try:
						args = explode_args(message)
					except ExplodeError:
						error_message = 'invalid_syntax'
					if error_message:
						self.send_message('error %s' % error_message)

					if args:
						command, args = args[0], args[1:]
						if command == 'exit':
							break
						elif command == 'debug':
							if len(args) >= 1:
								print '%d: %s' % (self.id, args[0])
						on_message(self.id, command, args)
		finally:
			print 'channel ended'
			on_finished(self.id)

	def close(self):
		self.io.close()

	def cleanup(self):
		self.io.cleanup()

class Server(object):
	def __init__(self, model):
		self.channels = {}
		self.model = model
		self.message_semaphore = threading.Semaphore(0)
		self.message_queue = ServerChannelMessageQueue()
		self.finished = False

	def add_channel(self, channel):
		self.channels[channel.id] = channel

	def listen_to_channel(self, channel_id):
		def channel_finished(id):
			self.message_queue.append(ServerChannelMessage(id, None, None))
			self.message_semaphore.release()
		def message_received(id, command, args):
			self.message_queue.append(ServerChannelMessage(id, command, args))
			self.message_semaphore.release()

		channel = self.channels[channel_id]
		channel.listen(message_received, channel_finished)

	def start(self):
		server = self
		class Thread(threading.Thread):
			def run(self):
				while not server.finished:
					server.message_semaphore.acquire()
					message = server.message_queue.pop()
					if not message.command:
						server.cleanup_channel(message.id)
					else:
						server.handle_message(message.id, message.command, message.args)
				for channel in server.channels.itervalues():
					channel.close()
				print 'server thread exitting'
		thread = Thread()
		thread.start()

	def handle_message(self, id, command, args):
		channel = self.channels[id]
		self.model.handle_message(channel.agent, command, args, self)

	def cleanup_channel(self, id):
		channel = self.channels[id]
		del self.channels[id]
		channel.cleanup()
		self.model.handle_disconnect(channel.agent, self)
		num_master_channels = len([x for x in self.channels.itervalues()
			if x.master_channel])
		if num_master_channels == 0:
			self.finished = True

	def send_message(self, agent, message):
		channel = self.channels[agent.channel_id]
		channel.send_message(message)

	def kick(self, agent):
		print 'kicking agent %d' % agent.id
		channel = self.channels[agent.channel_id]
		channel.close()

class StdServerChannelIO(ServerChannelIO):
	def __init__(self):
		ServerChannelIO.__init__(self)
		self.eof = False

	def read_message(self):
		message = sys.stdin.readline()
		if not message:
			self.eof = True
		return message

	def send_message(self, message):
		sys.stdout.write(message + '\n')

	def is_end(self):
		return self.eof

	def cleanup(self):
		pass

	def close(self):
		pass

def create_std_server_channel(id, model):
	io = StdServerChannelIO()
	privileges = GameServerAgentPrivileges(admin_privs=True)
	agent = model.create_agent(id, privileges)
	channel = ServerChannel(id, io, agent, True)
	return channel

class ChildProcessServerChannelIO(ServerChannelIO):
	def __init__(self, cmd):
		self.eof = False
		ServerChannelIO.__init__(self)
		self.process = subprocess.Popen([cmd], shell=True, bufsize=1,
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		print 'process =', self.process.pid

	def read_message(self):
		message = self.process.stdout.readline()
		if not message:
			self.eof = True
		return message

	def send_message(self, message):
		try:
			self.process.stdin.write(message + '\n')
		except ValueError:
			pass

	def is_end(self):
		return self.eof

	def cleanup(self):
		self.process.stdin.close()

	def close(self):
		self.process.stdin.close()
		print 'waiting for engine process (pid=%d) to exit...' % self.process.pid
		retcode = self.process.wait()
		print 'process exitted with return code %d' % retcode

def create_child_process_server_channel(id, model, cmd):
	io = ChildProcessServerChannelIO(cmd)
	privileges = GameServerAgentPrivileges(admin_privs=True)
	agent = model.create_agent(id, privileges)
	channel = ServerChannel(id, io, agent)
	return channel

class GameServerAgentPrivileges(object):
	def __init__(self, admin_privs=False):
		self.admin_privs = admin_privs

class GameServerAgent(object):
	def __init__(self, id, channel_id, privileges):
		self.id = id
		self.channel_id = channel_id
		self.name = '<UNSET>'
		self.game = None
		self.player_indices = set()
		self.privileges = privileges

	def set_game(self, game):
		self.game = game

	def add_player_index(self, player_index):
		self.player_indices.add(player_index)

	def handle_message(self, command, args, model, server):
		if command == 'kick':
			if self.privileges.admin_privs:
				try:
					agent_string, = args
					agent_id = int(agent_string)
					agent = model.agents[agent_id]
				except:
					agent = None
				if agent:
					server.kick(agent)
				else:
					server.send_message(self, 'error invalid_user')

			else:
				server.send_message(self, 'error permission_denied %s' % command)
		else:
			if self.game:
				self.game.handle_message(command, args, self, server)
			else:
				server.send_message(self, 'error no_game_selected %s' % command)

	def handle_disconnect(self, model, server):
		if self.game:
			self.game.handle_disconnect(self, server)

	def set_name(self, name):
		self.name = name

class GameServerModel(object):
	def __init__(self, game_factory):
		self.game_factory = game_factory
		self.agents = {}
		self.last_agent_id = 0
		self.games = {}
		self.last_game_id = 0

	def create_agent(self, channel_id, privileges):
		id = self.alloc_agent_id()
		agent = GameServerAgent(id, channel_id, privileges)
		self.agents[id] = agent
		return agent

	def handle_message(self, agent, command, args, server):
		agent.handle_message(command, args, self, server)

	def handle_disconnect(self, agent, server):
		agent.handle_disconnect(self, server)
		del self.agents[agent.id]

	def create_game(self):
		id = self.alloc_game_id()
		game = self.game_factory(id)
		self.games[id] = game
		return game

	def start_game(self, game, server):
		game.start(server)

	def alloc_agent_id(self):
		id, self.last_agent_id = self.last_agent_id, self.last_agent_id + 1
		return id

	def alloc_game_id(self):
		id, self.last_game_id = self.last_game_id, self.last_game_id + 1
		return id

class ScrabblePlayer(object):
	def __init__(self, index, agent):
		self.index = index
		self.agent = agent
		self.rack = {}
		self.score = 0

class ScrabbleMove(object):
	horizontal = 1
	vertical = 2
	def __init__(self, start, direction, letters):
		self.start = start
		self.direction = direction
		self.letters = letters

	def __str__(self):
		row, col = self.start
		if self.direction == self.horizontal:
			pos_str = '%d%c' % (row, chr(ord('a') + col))
		else:
			pos_str = '%c%d' % (chr(ord('a') + col), row)
		
		word_str = ''
		for letter in self.letters:
			word_str += letter

		return '%s %s' % (pos_str, word_str)

class ParseMoveError(Exception):
	pass

parse_move_position_vertical_re = re.compile('^([a-z])([0-9]+)$')
parse_move_position_horizontal_re = re.compile('^([0-9]+)([a-z])$')
def parse_move(position_string, letter_string):
	m = parse_move_position_vertical_re.match(position_string)
	direction = -1
	if m:
		direction = ScrabbleMove.vertical
		col_string, row_string = m.groups()
	if direction < 0:
		m = parse_move_position_horizontal_re.match(position_string)
		if m:
			direction = ScrabbleMove.horizontal
			row_string, col_string = m.groups()

	if direction < 0:
		raise ParseMoveError()

	row = int(row_string) - 1
	if row < 0 or row >= ScrabbleGame.num_rows:
		raise ParseMoveError()
	col = ord(col_string) - ord('a')
	if col < 0 or col >= ScrabbleGame.num_cols:
		raise ParseMoveError()

	letters = []
	i = 0
	while i < len(letter_string):
		c = letter_string[i].lower()
		i += 1
		if ord(c) < ord('a') or ord(c) > ord('z'):
			raise ParseMoveError()
		letters.append(c)

	return ScrabbleMove((row, col), direction, letters)

class ScrabbleGame(object):
	num_rows = 15
	num_cols = 15
	initial_tiles = 7
	letter_scores = ({
		'a': 1,
		'b': 3,
		'c': 3,
		'd': 2,
		'e': 1,
		'f': 4,
		'g': 2,
		'h': 4,
		'i': 1,
		'j': 8,
		'k': 5,
		'l': 1,
		'm': 3,
		'n': 1,
		'o': 1,
		'p': 3,
		'q': 10,
		'r': 1,
		's': 1,
		't': 1,
		'u': 1,
		'v': 4,
		'w': 4,
		'x': 8,
		'y': 4,
		'z': 10})
	letter_frequencies = ({
		'a': 9,
		'b': 2,
		'c': 2,
		'd': 4,
		'e': 1,
		'f': 2,
		'g': 3,
		'h': 2,
		'i': 9,
		'j': 1,
		'k': 1,
		'l': 4,
		'm': 2,
		'n': 6,
		'o': 8,
		'p': 2,
		'q': 1,
		'r': 6,
		's': 4,
		't': 6,
		'u': 4,
		'v': 2,
		'w': 2,
		'x': 1,
		'y': 2,
		'z': 1,
		'_': 2})

	def __init__(self, id, word_list):
		self.id = id
		self.players = []
		self.agents = set()
		self.to_move = -1
		self.word_list = word_list
		self.board = ([([None for x in xrange(self.num_cols)]) for y in xrange(self.num_rows)])
		self.pool = sum(([x for i in xrange(n)] for x, n in self.letter_frequencies.iteritems()), [])
		random.shuffle(self.pool)

	def handle_message(self, command, args, agent, server):
		if command == 'move':
			move = None
			if len(args) == 2:
				try:
					position, word = args
					move = parse_move(position, word)
				except ParseMoveError:
					pass
			if move:
				self.request_move(agent, server, move)
			else:
				server.send_message(agent, 'error move_syntax')
		elif command == 'get_word_list':
			self.send_word_list(agent, server)
		elif command == 'get_rack':
			try:
				player_index_string, = args
				player_index = int(player_index_string)
			except Exception:
				player_index = -1
			self.send_rack(agent, server, player_index)
		else:
			server.send_message(agent, 'error unknown_command %s' % command)

	def handle_disconnect(self, agent, server):
		for player_index in agent.player_indices:
			self.players[player_index].agent = None
		self.agents.remove(agent)
		for player_index in agent.player_indices:
			self.broadcast(server, 'dropped %d' % player_index)

	def add_player(self, agent):
		index = len(self.players)
		player = ScrabblePlayer(index, agent)
		self.players.append(player)
		self.agents.add(agent)
		return player

	def add_watcher(self, agent):
		self.agents.add(agent)

	def start(self, server):
		for player_index, player in enumerate(self.players):
			for i in xrange(self.initial_tiles):
				self.draw_tile(player)

		for player_index, player in enumerate(self.players):
			server.send_message(player.agent, 'start_game')
			server.send_message(player.agent, 'player_index %d' % player_index)

		for player_index, player in enumerate(self.players):
			self.broadcast(server, 'player %d %s' %
				(player_index, player.agent.name))

		self.to_move = 0
		self.print_board()
		self.print_rack(self.players[self.to_move])
		self.prompt_turn(server)

	def print_board(self):
		sys.stdout.write('   ')
		for col in xrange(self.num_cols):
			sys.stdout.write(chr(ord('a') + col))
		sys.stdout.write(' \n')
		sys.stdout.write('  +')
		for col in xrange(self.num_cols):
			sys.stdout.write('-')
		sys.stdout.write('+\n')
		for row in xrange(self.num_rows):
			sys.stdout.write('%2d' % (row + 1))
			sys.stdout.write('|')
			for col in xrange(self.num_cols):
				tile = self.board[row][col]
				if not tile:
					sys.stdout.write('.')
				else:
					sys.stdout.write(tile)
			sys.stdout.write('|\n')
		sys.stdout.write('  +')
		for col in xrange(self.num_cols):
			sys.stdout.write('-')
		sys.stdout.write('+\n')

	def print_rack(self, player):
		for tile, count in player.rack.iteritems():
			for j in xrange(count):
				sys.stdout.write('%c ' % tile)
		sys.stdout.write('\n')

	def prompt_turn(self, server):
		self.broadcast(server, 'to_move %d' % self.to_move)

	def request_move(self, agent, server, move):
		if self.to_move in agent.player_indices:
			try:
				player = self.players[self.to_move]

				move_score = self.make_move(player, move)
				self.broadcast(server, 'move_made %d %s %d' %
					(self.to_move, str(move), move_score))
				player.score += move_score

				for i in xrange(self.initial_tiles - sum(player.rack.itervalues())):
					self.draw_tile(player)

				self.to_move = (self.to_move + 1) % len(self.players)
				self.print_board()
				self.print_rack(self.players[self.to_move])
				self.prompt_turn(server)
			except self.InvalidMove:
				server.send_message(agent, 'error move_invalid')

		else:
			server.send_message(agent, 'error not_to_move')

	class InvalidMove(Exception):
		pass
	def make_move(self, player, move):
		board = copy.deepcopy(self.board)
		rack = copy.deepcopy(player.rack)
		score = 0
		row, col = move.start
		dir_x, dir_y = {ScrabbleMove.horizontal: (1, 0), ScrabbleMove.vertical: (0, 1)}[move.direction]
		other_x, other_y = {ScrabbleMove.horizontal: (0, 1), ScrabbleMove.vertical: (1, 0)}[move.direction]
		words_made = []
		words_made.append((col, row, dir_x, dir_y))
		for letter_index in xrange(len(move.letters)):
			tile_x, tile_y = col + dir_x * letter_index, row + dir_y * letter_index
			if tile_x < 0 or tile_x >= self.num_cols:
				raise self.InvalidMove()
			if tile_y < 0 or tile_y >= self.num_rows:
				raise self.InvalidMove()
			current_tile = board[tile_y][tile_x]
			tile = move.letters[letter_index]
			if current_tile != None and current_tile != tile:
				raise self.InvalidMove()
			tile_count = rack.get(tile, 0)
			if current_tile != tile:
				if tile_count < 1:
					raise self.InvalidMove()
				rack[tile] = tile_count - 1
			board[tile_y][tile_x] = tile
			words_made.append((tile_x, tile_y, other_x, other_y))

		for pos_x, pos_y, word_dir_x, word_dir_y in words_made:
			offsets = [0, 0]
			scales = [-1, 1]
			for offset_index in xrange(2):
				for i in xrange(1, 100000):
					test_x = pos_x + scales[offset_index] * word_dir_x * i
					test_y = pos_y + scales[offset_index] * word_dir_y * i
					if test_x < 0 or test_x >= self.num_cols:
						break
					if test_y < 0 or test_y >= self.num_rows:
						break
					c = board[test_y][test_x]
					if c == None:
						break
					offsets[offset_index] += scales[offset_index]

			start_offset, end_offset = offsets
			length = end_offset - start_offset + 1

			if length > 1:
				word = ''
				for i in xrange(length):
					tile_x = pos_x + (start_offset + i) * word_dir_x
					tile_y = pos_y + (start_offset + i) * word_dir_y
					letter = board[tile_y][tile_x]
					word += letter
					score += self.letter_scores[letter]

				print word
				if word not in self.word_list:
					raise self.InvalidMove()

		self.board = board
		player.rack = rack

		return score

	def draw_tile(self, player):
		tile = self.pool.pop()
		player.rack[tile] = player.rack.get(tile, 0) + 1

	def send_word_list(self, agent, server):
		server.send_message(agent, 'word_count %d' % len(self.word_list))
		for index, word in enumerate(self.word_list):
			server.send_message(agent, 'word %d %s' % (index, word))

	def send_rack(self, agent, server, player_index):
		if player_index in agent.player_indices:
			player = self.players[player_index]
			server.send_message(agent, 'tile_count %d' % sum(player.rack.itervalues()))
			i = 0
			for tile, count in player.rack.iteritems():
				for j in xrange(count):
					server.send_message(agent, 'tile %d %c' % (i, tile))
					i += 1
		else:
			server.send_message(agent, 'error invalid_player_index')

	def broadcast(self, server, message):
		for agent in self.agents:
			server.send_message(agent, message)

class AppOptions(object):
	execute_none = 0
	execute_game = 1
	execute_dummy_engine = 2

	def __init__(self):
		self.execute_mode = self.execute_none
		self.child_engines = []
		self.word_list_path = None

class OptionArgumentMissingError(Exception):
	pass

def parse_command_line(argv):

	options = AppOptions()
	args = argv[1:]

	if len(args) > 0 and args[0][0] != '-':
		try:
			command = args.pop(0).strip().lower()
		except IndexError:
			command = None
		if command == 'game':
			options.execute_mode = AppOptions.execute_game
		elif command == 'dummy_engine':
			options.execute_mode = AppOptions.execute_dummy_engine

	if options.execute_mode == AppOptions.execute_none:
		options.execute_mode = AppOptions.execute_game

	while args:
		arg = args.pop(0).strip().lower()

		try:
			if arg == '-e' or arg == '--engine':
				path = args.pop(0)
				options.child_engines.append(path)
			if arg == '-w' or arg == '--words':
				path = args.pop(0)
				options.word_list_path = path
		except IndexError:
			raise OptionArgumentMissingError('The option "%s" requires an argument.' % arg)

	return options

class WordListLoadError(Exception):
	pass
def load_word_list(word_list_path):
	word_re = re.compile(r'^([a-z]+)$')
	words = set()
	try:
		f = file(word_list_path, 'r')
	except IOError:
		raise WordListLoadError('word_list_path')
	for line in f:
		candidate = line.strip()
		m = word_re.match(candidate)
		if m:
			word = m.group(0)
			words.add(word)
	return words

def run_game(args, child_engines, word_list_path):
	try:
		word_list = load_word_list(word_list_path)
	except WordListLoadError, e:
		print 'unable to load word list from "word_list_path".'
		return
	def create_game(id):
		return ScrabbleGame(id, word_list)
	model = GameServerModel(create_game)
	server = Server(model)
	server.start()

	std_channel = create_std_server_channel(0, model)
	std_channel.agent.set_name('player%d' % std_channel.agent.id)
	server.add_channel(std_channel)

	game = model.create_game()

	for engine in child_engines:

		if engine == '-':

			std_agent_player = game.add_player(std_channel.agent)
			std_channel.agent.set_game(game)
			std_channel.agent.add_player_index(std_agent_player.index)

		else:

			child_process_channel = create_child_process_server_channel(1, model, engine)
			child_process_channel.agent.set_name('player%d' % child_process_channel.agent.id)
			server.add_channel(child_process_channel)

			class Thread(threading.Thread):
				def run(self):
					server.listen_to_channel(child_process_channel.id)
			child_thread = Thread()
			child_thread.setDaemon(True)
			child_thread.start()

			child_agent_player = game.add_player(child_process_channel.agent)
			child_process_channel.agent.set_game(game)
			child_process_channel.agent.add_player_index(child_agent_player.index)

	std_channel.agent.set_game(game)
	game.add_watcher(std_channel.agent)

	model.start_game(game, server)

	server.listen_to_channel(std_channel.id)

	print 'main thread exitting'

class DummyEngine(object):
	class InputError(Exception): pass
	def run(self):
		while True:
			try:
				for command, args in self.read_commands():
					pass
				break
			except DummyEngine.InputError, e:
				print 'debug "Invalid command syntax received from server: \"%s\""' % e.message.strip()
				sys.stdout.flush()
		print ' debug "exitting"'
		sys.stdout.flush()
	
	def read_commands(self):
		while True:
			message = sys.stdin.readline()
			if not message:
				break
			args = None
			try:
				args = explode_args(message)
			except ExplodeError:
				raise self.InputError(message)

			if args:
				command, args = args[0], args[1:]
				yield command, args

def main(argv):

	options = None
	try:
		options = parse_command_line(argv)
	except OptionArgumentMissingError, e:
		print e.message

	args_error = None
	if options:
		if options.execute_mode == AppOptions.execute_game:
			if len(options.child_engines) < 2:
				args_error = 'at least 2 engines must be specified on command line using --engine.'
			elif not options.word_list_path:
				args_error = 'a file containing the list of valid words must be specified using --words.'
			else:
				run_game(argv, options.child_engines, options.word_list_path)
					
		elif options.execute_mode == AppOptions.execute_dummy_engine:
			DummyEngine().run()
		else:
			args_error = ''
	else:
		args_error = ''
		
	if args_error != None:
		if args_error:
			print 'netscrabble error:', args_error
		print '  Usage: %s <command> [options]' % argv[0]
		print '    where <command> is one of:'
		print '     * server'
		print '     * dummy-engine'
		print '    and options can include:'
		print '     * -e|--engine <path>'
		print '     * -w|--words <path>'

if __name__ == '__main__':
	main(sys.argv)

