#!/usr/bin/python
# 
# Copyright 2011-2012 Jeff Bush
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# 

import sys, shlex, copy, math

TAG_INTEGER = 0		# Make this zero because types default to this when pushed
TAG_CONS = 1
TAG_FUNCTION = 2

OP_NOP = 0
OP_CALL = 1
OP_RETURN = 2
OP_POP = 3
OP_LOAD = 4
OP_STORE = 5
OP_ADD = 6
OP_SUB = 7
OP_REST = 8
OP_GTR = 9
OP_GTE = 10
OP_EQ = 11
OP_NEQ = 12
OP_DUP = 13
OP_GETTAG = 14
OP_SETTAG = 15
OP_AND = 16
OP_OR = 17
OP_XOR = 18
OP_LSHIFT = 19
OP_RSHIFT = 20
OP_GETBP = 21
OP_RESERVE = 24		# High bits indicates this has a parameter
OP_PUSH = 25
OP_GOTO = 26
OP_BFALSE = 27
OP_GETLOCAL = 29
OP_SETLOCAL = 30
OP_CLEANUP = 31

class Symbol:
	LOCAL_VARIABLE = 1
	GLOBAL_VARIABLE = 2
	FUNCTION = 3

	def __init__(self, type):
		self.type = type
		self.index = -1
		self.initialized = False	# For globals
		self.function = None
		self.upval = None

# Labels are only visible inside the functions they are defined in.
class Label:
	def __init__(self):
		self.defined = False
		self.address = 0

class Function:
	def __init__(self):
		self.name = None
		self.localFixups = []
		self.baseAddress = 0
		self.referenced = False		# Used to strip dead functions
		self.numLocalVariables = 0
		self.instructions = []		# Each entry is a word
		self.environment = [{}]		# Stack of scopes
		self.closureVars = []
		self.enclosingFunction = None

		# Save a spot for an initial 'reserve' instruction
		self.emitInstruction(OP_RESERVE, 0)
		
		# Entry label comes after reserve
		self.entry = self.generateLabel()
		self.emitLabel(self.entry)

	def generateLabel(self):
		return Label()

	def getEntryLabel(self):
		return self.entry

	def enterScope(self):
		self.environment += [{}]

	def exitScope(self):
		self.environment.pop()
		
	def lookupLocalVariable(self, name):
		for scope in reversed(self.environment):
			if name in scope:
				return scope[name]
				
		return None
		
	def reserveLocalVariable(self, name):
		sym = Symbol(Symbol.LOCAL_VARIABLE)
		self.environment[-1][name] = sym
		sym.index = -(self.numLocalVariables + 2)	# Skip return address and base pointer
		self.numLocalVariables += 1
		return sym

	def reserveParameter(self, name, index):
		sym = Symbol(Symbol.LOCAL_VARIABLE)
		self.environment[-1][name] = sym
		sym.index = index + 1

	def getProgramAddress(self):
		return len(self.instructions)

	def emitLabel(self, label):
		assert not label.defined
		label.defined = True
		label.address = self.getProgramAddress()

	def emitBranchInstruction(self, op, label):
		self.emitInstruction(op, 0)
		self.localFixups += [( self.getProgramAddress() - 1, label)]

	def emitInstruction(self, op, param = 0):
		if param > 32767 or param < -32768:
			raise Exception('param out of range ' + str(param));
	
		# Convert to two's complement
		if param < 0:
			param = ((-param ^ 0xffff) + 1) & 0xffff

		self.instructions += [ (op << 16) | param]

	def patch(self, offset, value):
		self.instructions[offset] &= ~0xffff
		self.instructions[offset] |= (value & 0xffff)

	def performLocalFixups(self):
		self.instructions[0] = (OP_RESERVE << 16) | self.numLocalVariables
		for ip, label in self.localFixups:
			if not label.defined:
				raise Exception('undefined label')

			self.patch(ip, self.baseAddress + label.address)

#
# The parser just converts ASCII data into a nested set of python lists that represent
# the structure of the program.
#
class Parser:
	def __init__(self):
		self.lexer = None
		self.program = []
		self.filename = None

	def parseFile(self, filename):
		self.filename = filename
		stream = open(filename, 'r')
		self.lexer = shlex.shlex(stream)
		self.lexer.commenters = ';'
		self.lexer.quotes = '"'
		self.lexer.wordchars += '?+<>!@#$%^&*;:.=-_'

		while True:
			expr = self.parseExpr()
			if expr == '':
				break
				
			self.program += [ expr ]
			
		stream.close()

	def parseParenList(self):
		list = []
		while True:
			lookahead = self.lexer.get_token()
			if lookahead == '':
				print 'missing )'
				break
			elif lookahead == ')':
				break
	
			self.lexer.push_token(lookahead)			
			list += [ self.parseExpr() ]
			
		return list
	
	def parseExpr(self):
		token = self.lexer.get_token()
		if token == '':
			return ''
		
		elif token == '\'':
			return [ 'quote', self.parseExpr() ]
		elif token == '`':
			return [ 'backquote', self.parseExpr() ]
		elif token == ',':
			return [ 'unquote', self.parseExpr() ]
		elif token == '(':
			return self.parseParenList()
		elif token.isdigit() or (token[0] == '-' and len(token) > 1):
			return int(token)
		elif token == ')':
			raise Exception('unmatched ), ' + self.filename + ':' + str(self.lexer.lineno))
		else:
			return token
			
	def getProgram(self):
		return self.program

class Compiler:
	def __init__(self):
		self.globals = {}
		self.currentFunction = Function()
		self.functionList = [ 0 ]		# We reserve a spot for 'main'
		self.breakStack = []

		# Can be a fixup for:
		#   - A global variable 
		#   - A function pointer
		# Each stores ( function, functionOffset, target )
		self.globalFixups = []	

	# 
	# Lookup a symbol, starting in the current scope and working backward
	# to enclosing scopes.  If the symbol doesn't exist, create one in the global
	# namespace.
	#
	def lookupSymbol(self, name):
		# Check for local variable
		sym = self.currentFunction.lookupLocalVariable(name)
		if sym != None:
			return sym

		# Is this an upval?
		isUpval = False
		func = self.currentFunction.enclosingFunction
		while func != None:
			sym = func.lookupLocalVariable(name)
			if sym != None:
				isUpval = True
				break

			func = func.enclosingFunction

		if isUpval:
			raise Exception('closures not implemented: variable ' + name + ' defined in enclosing function')
			
#	XXX partial implementation of closure variables, currently disabled.	
#			# Create a new local variable
#			sym = self.currentFunction.reserveLocalVariable(name)
#			self.currentFunction.closureVars += [ sym ]
#
#			# Mark free variables, possibly through multiple functions
#			dest = sym
#			func = self.currentFunction.enclosingFunction
#			foundSource = False
#			while func != None and not foundSource:
#				source = func.lookupLocalVariable(name)
#				if source == None:
#					# Note that, when we create an anonymous function, we will
#					# open a new scope that will contain these temporaries.
#					# They will go out of scope when we finish compiling the function
#					# We DO want to be sure to create a named variable so we can
#					# share the same instance if the outer variable is referenced
#					# multiple times.  Eg:
#					# (function foo (a) (function bar (b) (function baz(c) (+ c a))))
#					# A temporary 'a' will be created in the outer scope of bar.
#					#
#					source = self.currentFunction.reserveLocalVariable(name)	
#				else:
#					foundSource = True
#
#				dest.upval = source
#				func.closureVars += [ source ]
#				func = func.enclosingFunction
#				
#			return sym

		# Check for global variable
		if name in self.globals:
			return self.globals[name]

		# Not found, create a new global variable implicitly
		sym = Symbol(Symbol.GLOBAL_VARIABLE)
		sym.index = len(self.globals)		# Allocate a storage slot for this
		self.globals[name] = sym
		return sym

	#
	# Top level compile function.  All code not in function blocks will be 
	# emitted into an implicitly created dummy function 'main'
	#
	def compile(self, program):
		self.currentFunction = Function()
		self.currentFunction.referenced = True
		self.currentFunction.name = '<main>'

		# create a built-in variable that indicates where the heap starts
		# (will be patched at the end of compilation with the proper address)
		heapstart = self.lookupSymbol('$heapstart')
		heapstart.initialized = True
		self.currentFunction.emitInstruction(OP_PUSH, 0)
		self.currentFunction.emitInstruction(OP_PUSH, 0)
		self.currentFunction.emitInstruction(OP_STORE);
		self.currentFunction.emitInstruction(OP_POP);

		for expr in program:
			if expr[0] == 'function':
				self.compileFunction(expr)
			else:
				self.compileExpression(expr)
				self.currentFunction.emitInstruction(OP_POP) # Clean up stack

		# Put an infinite loop at the end 
		forever = self.currentFunction.generateLabel()
		self.currentFunction.emitLabel(forever)
		self.currentFunction.emitBranchInstruction(OP_GOTO, forever)
		
		# The top level code (which looks like a function) is the first code
		# emitted, since that's where execution will start
		self.functionList[0] = self.currentFunction

		# Strip out functions that aren't called
		self.functionList = filter(lambda x: x.referenced, self.functionList)

		# Need to determine where functions are in memory
		self.codeLength = 0
		for func in self.functionList:
			func.baseAddress = self.codeLength
			self.codeLength += len(func.instructions)

		# Do fixups
		for function in self.functionList:
			function.performLocalFixups()		# Replace labels with offsets

		self.performGlobalFixups()

		# Fix up the global variable size table (we know it is the push right
		# after reserve)
		self.functionList[0].patch(1, len(self.globals))

		# For debugging: create a listing of the instructions used.
		listfile = open('program.lst', 'wb')

		# Write out table of global variables
		listfile.write('Globals:\n')
		for var in self.globals:
			sym = self.globals[var]
			if sym.type != Symbol.FUNCTION:
				listfile.write(' ' + var + ' var@' + str(sym.index) + '\n')

		for func in self.functionList:
			listfile.write('\nfunction ' + str(func.name) + '\n')
			disassemble(listfile, func.instructions, func.baseAddress)

		# Write out expanded expressions
		prettyPrintSExpr(listfile, program, 0)

		listfile.close()
		
		# Now consolidate the functions
		instructions = []
		for func in self.functionList:
			instructions += func.instructions		
		
		return instructions

	#
	# Compile named function definition (function name (param param...) body)
	#
	def compileFunction(self, expr):
		function = self.compileFunctionBody(expr[1], expr[2], expr[3:])
		self.functionList += [ function ]
	
		if expr[1] in self.globals:
			# There was a forward reference to this function and it was 
			# assumed to be a global.  We're kind of stuck with that now,
			# because code was generated that can't be easily fixed up.
			# Emit code to stash it in the global table.
			sym = self.globals[expr[1]]
			if sym.initialized:
				raise Exception('Global variable ' + expr[1] + ' redefined as function')

			# Function address, to be fixed up
			self.currentFunction.emitInstruction(OP_PUSH, 0)
			self.globalFixups += [ ( self.currentFunction,
				self.currentFunction.getProgramAddress() - 1, function ) ]

			# Global variable offset, to be fixed up
			self.currentFunction.emitInstruction(OP_PUSH, 0)
			self.globalFixups += [ ( self.currentFunction,
				self.currentFunction.getProgramAddress() - 1, sym ) ]
			self.currentFunction.emitInstruction(OP_STORE);
			self.currentFunction.emitInstruction(OP_POP);
			sym.initialized = True
			function.referenced = True
		else:
			sym = Symbol(Symbol.FUNCTION)
			sym.initialized = True
			sym.function = function


		self.globals[expr[1]] = sym

	#
	# The mother compile function, from which all useful things are compiled.
	# Compiles an arbitrary lisp expression.  Each expression consumes
	# all parameters, which will be pushed onto the stack from right to left,
	# and leave a result value on the stack.  All expressions have values in LISP.
	#
	# Determining if something is a tail call is fairly simple in S-Expression form.
	# The outermost form is generally a tail call.  This propagates through control
	# flow forms and to the last expression in a sequence.
	#
	def compileExpression(self, expr, isTailCall=False):
		if isinstance(expr, list):
			if len(expr) == 0:
				# Empty expression
				self.currentFunction.emitInstruction(OP_PUSH, 0)
			else:
				self.compileCombination(expr, isTailCall)
		elif isinstance(expr, int):
			self.compileIntegerLiteral(expr)
		elif expr[0] == '"':
			self.compileString(expr[1:-1])
		elif expr == 'nil' or expr == 'false':
			self.currentFunction.emitInstruction(OP_PUSH, 0)
		elif expr == 'true':
			self.currentFunction.emitInstruction(OP_PUSH, 1)
		else:
			# This is a variable.
			self.compileIdentifier(expr)

	def compileIntegerLiteral(self, expr):
		self.currentFunction.emitInstruction(OP_PUSH, expr)

	# 
	# Look up the variable in the current environment, push the contents
	# of the variable onto the stack.
	#
	def compileIdentifier(self, expr):
		variable = self.lookupSymbol(expr)
		if variable.type == Symbol.LOCAL_VARIABLE:
			self.currentFunction.emitInstruction(OP_GETLOCAL, 
				variable.index)
		elif variable.type == Symbol.GLOBAL_VARIABLE:
			self.currentFunction.emitInstruction(OP_PUSH, 0)
			self.globalFixups += [ ( self.currentFunction,
				self.currentFunction.getProgramAddress() - 1, variable ) ]
			self.currentFunction.emitInstruction(OP_LOAD);
		elif variable.type == Symbol.FUNCTION:
			variable.function.referenced = True
			self.currentFunction.emitInstruction(OP_PUSH, 0)
			self.globalFixups += [ ( self.currentFunction,
				self.currentFunction.getProgramAddress() - 1, variable ) ]
		else:
			raise Exception('internal error: symbol does not have a valid type', '')

	#
	# A combination is basically a list expression (op param param...)
	# This may be a special form or a function call.
	# Anything that isn't an atom or a number is going to be compiled here.
	#
	def compileCombination(self, expr, isTailCall=False):
		if isinstance(expr[0], str):
			functionName = expr[0]
			if functionName in self.PRIMITIVES:
				self.compilePrimitive(expr)
			elif functionName == 'function':
				self.compileAnonymousFunction(expr)
			elif functionName == 'begin':
				self.compileSequence(expr[1:], isTailCall)
			elif functionName == 'while':
				self.compileWhile(expr)
			elif functionName == 'break':
				self.compileBreak(expr)
			elif functionName == 'if':		# Special forms handling starts here
				self.compileIf(expr, isTailCall)
			elif functionName == 'assign':
				self.compileAssign(expr)
			elif functionName == 'quote':
				self.compileQuote(expr[1])
			elif functionName == 'let':
				self.compileLet(expr, isTailCall)
			elif functionName == 'getbp':
				self.currentFunction.emitInstruction(OP_GETBP)
			elif functionName == 'and' or functionName == 'or' or functionName == 'not':
				self.compileBooleanExpression(expr)	
			else:
				# Anything that isn't a built in form falls through to here.
				# (call to a user defined function)
				self.compileFunctionCall(expr, isTailCall)
		else:
			self.compileFunctionCall(expr)

	def compileBasePointer(self, expr):
		self.currentFunction.emitInstruction(OP_GETLOCAL, 0)

	#
	# In order to handle quoted expressions, we need to emit a bunch of cons
	# calls.
	#
	def compileQuote(self, expr):
		if isinstance(expr, list):
			if len(expr) == 3 and expr[1] == '.':
				# This is a pair of the for ( expr . expr )
				# Create a single cons cell for it.
				self.compileQuote(expr[2])
				self.compileQuote(expr[0])

				# Cons is not an instruction.  Emit a call to the 
				# library function
				self.compileIdentifier('cons')
				self.currentFunction.emitInstruction(OP_CALL)
				self.currentFunction.emitInstruction(OP_CLEANUP, 2)
			elif len(expr) == 0:
				# Empty list
				self.compileIntegerLiteral(0)
			else:
				# List, create a chain of cons calls
				self.compileQuotedList(expr)
		elif isinstance(expr, int):
			self.compileIntegerLiteral(expr)
		else:
			self.compileString(expr)

	def compileQuotedList(self, tail):
		if len(tail) == 1:
			self.compileIntegerLiteral(0)
		else:
			# Do the tail first to avoid allocating too many temporaries
			self.compileQuotedList(tail[1:])

		self.compileQuote(tail[0])

		# Cons is not an instruction.  Emit a call to the library function
		self.compileIdentifier('cons')
		self.currentFunction.emitInstruction(OP_CALL)
		self.currentFunction.emitInstruction(OP_CLEANUP, 2)

	#
	# Strings just compile down to lists of characters, since there is not
	# a native string type.
	#
	def compileString(self, string):
		if len(string) == 1:
			self.compileIntegerLiteral(0)
		else:
			self.compileString(string[1:])

		self.compileIntegerLiteral(ord(string[0]))

		# Cons is not an instruction.  Emit a call to the library function
		self.compileIdentifier('cons')
		self.currentFunction.emitInstruction(OP_CALL)
		self.currentFunction.emitInstruction(OP_CLEANUP, 2)

	# 
	# Set a variable (assign variable value)
	# This will also leave the value of the expression on the stack
	#
	def compileAssign(self, expr):
		variable = self.lookupSymbol(expr[1])
		if variable.type == Symbol.LOCAL_VARIABLE:
			self.compileExpression(expr[2])
			self.currentFunction.emitInstruction(OP_SETLOCAL, variable.index)
		elif variable.type == Symbol.GLOBAL_VARIABLE:
			self.compileExpression(expr[2])
			self.currentFunction.emitInstruction(OP_PUSH, 0)
			self.globalFixups += [ ( self.currentFunction,
				self.currentFunction.getProgramAddress() - 1, variable ) ]
			self.currentFunction.emitInstruction(OP_STORE);
			variable.initialized = True
		elif variable.type == Symbol.FUNCTION:
			raise Exception('Error: cannot assign function ' + expr[1])
		else:
			raise Exception('Internal error: what kind of variable is ' + expr[1] + '?', '')

	# 
	# Compile a boolean expression that is not part of an conditional form like
	# if or while. This will push the result (1 or 0) on the stack.
	#
	def compileBooleanExpression(self, expr):
		falseLabel = self.currentFunction.generateLabel()
		doneLabel = self.currentFunction.generateLabel()
		self.compilePredicate(expr, falseLabel)
		self.currentFunction.emitInstruction(OP_PUSH, 1)
		self.currentFunction.emitBranchInstruction(OP_GOTO, doneLabel);
		self.currentFunction.emitLabel(falseLabel)
		self.currentFunction.emitInstruction(OP_PUSH, 0)
		self.currentFunction.emitLabel(doneLabel)

	#
	# Compile a boolean expression that is part of a control flow expression.
	# If the expression is false, this will jump to the label 'falseLabel', otherwise
	# it will fall through.  This performs short circuit evaluation where possible.
	#
	def compilePredicate(self, expr, falseLabel):
		if isinstance(expr, list):
			if expr[0] == 'and':
				if len(expr) < 2:
					raise Exception('wrong number of arguments for and')

				# Note that we short circuit if any condition is false and
				# jump directly to the false case
				for cond in expr[1:]:
					self.compilePredicate(cond, falseLabel)

				return
			elif expr[0] == 'or':
				if len(expr) < 2:
					raise Exception('wrong number of arguments for or')

				trueTarget = self.currentFunction.generateLabel()
				for cond in expr[1:-1]:
					testNext = self.currentFunction.generateLabel()		
					self.compilePredicate(cond, testNext)
					self.currentFunction.emitBranchInstruction(OP_GOTO, trueTarget)
					self.currentFunction.emitLabel(testNext)

				# Final test simply short circuits to false target
				self.compilePredicate(expr[-1], falseLabel)
				self.currentFunction.emitLabel(trueTarget)
				return
			elif expr[0] == 'not':
				if len(expr) != 2:
					raise Exception('wrong number of arguments for not')

				skipTo = self.currentFunction.generateLabel()
				self.compilePredicate(expr[1], skipTo)
				self.currentFunction.emitBranchInstruction(OP_GOTO, falseLabel)
				self.currentFunction.emitLabel(skipTo)
				return

		self.compileExpression(expr)
		self.currentFunction.emitBranchInstruction(OP_BFALSE, falseLabel)

	#
	# Conditional execution of the form
	# (if expr true [false])
	#
	def compileIf(self, expr, isTailCall=False):
		falseLabel = self.currentFunction.generateLabel()
		doneLabel = self.currentFunction.generateLabel()

		self.compilePredicate(expr[1], falseLabel)
		self.compileExpression(expr[2], isTailCall)	# True part
		self.currentFunction.emitBranchInstruction(OP_GOTO, doneLabel);
		self.currentFunction.emitLabel(falseLabel)
		
		# False part
		if len(expr) > 3:
			self.compileExpression(expr[3], isTailCall)	
		else:
			self.currentFunction.emitInstruction(OP_PUSH, 0)
		
		self.currentFunction.emitLabel(doneLabel)


	#
	# Basic loop construct
	# (while condition body)
	# Note that body is implitly a (begin... and can use a sequence
	# If the loop terminates normally (the condition is false), the
	# result is zero.  If (break val) is called, 'val' will be the result.
	#
	def compileWhile(self, expr):
		topOfLoop = self.currentFunction.generateLabel()
		bottomOfLoop = self.currentFunction.generateLabel()
		breakLoop = self.currentFunction.generateLabel()
		self.breakStack += [ breakLoop ]
		self.currentFunction.emitLabel(topOfLoop)
		self.compilePredicate(expr[1], bottomOfLoop)
		self.compileSequence(expr[2:])
		self.currentFunction.emitInstruction(OP_POP) # Clean up stack
		self.currentFunction.emitBranchInstruction(OP_GOTO, topOfLoop)
		self.currentFunction.emitLabel(bottomOfLoop)
		self.breakStack.pop()
		self.currentFunction.emitInstruction(OP_PUSH, 0)	# Default value
		self.currentFunction.emitLabel(breakLoop)

	# break out of a loop 
	# (break [value])
	def compileBreak(self, expr):
		label = self.breakStack[-1]
		if len(expr) > 1:
			self.compileExpression(expr[1])	# Push value on stack
		else:
			self.currentFunction.emitInstruction(OP_PUSH, 0)

		self.currentFunction.emitBranchInstruction(OP_GOTO, label)

	# Primitives look like function calls, but map directly to machine instructions
 	PRIMITIVES = {
		'+' 		: ( OP_ADD, 2),
		'-' 		: ( OP_SUB, 2),
		'>'			: ( OP_GTR, 2),
		'>='		: ( OP_GTE, 2),
		'<'			: (-1, 2),	# These are handled by switching operand order
		'<='		: (-1, 2),
		'='			: (OP_EQ, 2),
		'<>'		: (OP_NEQ, 2),
		'load' 		: (OP_LOAD, 1),
		'store'		: (OP_STORE, 2),
		'first'		: (OP_LOAD, 1),
		'rest' 		: (OP_REST, 1),
		'second'	: (OP_REST, 1),	# Alias for rest
		'settag'	: (OP_SETTAG, 2),
		'gettag'	: (OP_GETTAG, 1),
		'bitwise-and' : (OP_AND, 2),
		'bitwise-or' : (OP_OR, 2),
		'bitwise-xor' : (OP_XOR, 2),
		'rshift'	: (OP_RSHIFT, 2),
		'lshift'	: (OP_LSHIFT, 2)
 	}

	def compilePrimitive(self, expr):
		opcode, nargs = self.PRIMITIVES[expr[0]]
	
		if len(expr) - 1 != nargs:
			raise Exception('wrong number of arguments for', expr[0])
	
		# Synthesize lt and lte operators by switching order and using the opposite operators
		if expr[0] == '<':
			self.compileExpression(expr[1])
			self.compileExpression(expr[2])
			self.currentFunction.emitInstruction(OP_GTR)
		elif expr[0] == '<=':
			self.compileExpression(expr[1])
			self.compileExpression(expr[2])
			self.currentFunction.emitInstruction(OP_GTE)
		else:
			if len(expr) > 2:
				self.compileExpression(expr[2])

			self.compileExpression(expr[1])
			self.currentFunction.emitInstruction(opcode)

	#
	# Call a function
	#
	def compileFunctionCall(self, expr, isTailCall = False):
		if isinstance(expr[0], int):
			raise Exception('Cannot use integer as function')

		# Push arguments
		for paramExpr in reversed(expr[1:]):
			self.compileExpression(paramExpr)

		if self.currentFunction.name == expr[0] and isTailCall:
			# This is a recursive call.  Copy parameters back into frame and
			# then jump to entry
			for x in range(len(expr) - 1):
				self.currentFunction.emitInstruction(OP_SETLOCAL, x + 1)
				self.currentFunction.emitInstruction(OP_POP)
			
			self.currentFunction.emitBranchInstruction(OP_GOTO, self.currentFunction.getEntryLabel())
		else:
			self.compileExpression(expr[0])
			self.currentFunction.emitInstruction(OP_CALL)
			if len(expr) > 1:
				self.currentFunction.emitInstruction(OP_CLEANUP, len(expr) - 1)

	#
	# Common code to compile body of the function definition (either anonymous or named)
	# ((param param...) body)
	#
	def compileFunctionBody(self, name, params, body):
		oldFunc = self.currentFunction
		newFunction = Function()
		newFunction.name = name
		newFunction.enclosingFunction = oldFunc
		self.currentFunction = newFunction
		
		for index, paramName in enumerate(params):
			self.currentFunction.reserveParameter(paramName, index)

		self.currentFunction.numParams = len(params)
	
		# Compile top level expression.
		self.compileSequence(body, isTailCall=True)
		self.currentFunction.emitInstruction(OP_RETURN)
		self.currentFunction = oldFunc

		return newFunction

	#
	# Of the form (function (param param...) (expr))
	# We generate the code in a separate top level, global function, then we will 
	# emit a reference to that code in the current function.
	#
	def compileAnonymousFunction(self, expr):
		# Note: we do an enterScope because we may create temporary variables to 
		# represent free variables while compiling. See lookupSymbol for more information.
		self.currentFunction.enterScope()
		newFunction = self.compileFunctionBody(None, expr[1], expr[2:])
		newFunction.referenced = True
		self.currentFunction.exitScope()

		newFunction.name = '<anonymous function>'

		# Now compile code that references the function object we created.  We'll
		# set the tag to indicate this is a function.
		self.currentFunction.emitInstruction(OP_PUSH, TAG_FUNCTION)
		self.currentFunction.emitInstruction(OP_PUSH, 0)
		self.globalFixups += [ ( self.currentFunction,
			self.currentFunction.getProgramAddress() - 1, newFunction ) ]
		self.currentFunction.emitInstruction(OP_SETTAG)
		self.functionList += [ newFunction ]

	#
	# A sequence of expressions.  The result will be the last expression evaluated.
	#
	def compileSequence(self, sequence, isTailCall=False):
		# Execute a sequence of statements
		# ...stmt1 stmt2 stmt3...)
		if len(sequence) == 0:
			self.currentFunction.emitInstruction(OP_PUSH, 0)	# Need to have at least one stmt
		else:
			for expr in sequence[:-1]:
				self.compileExpression(expr, isTailCall=False)
				self.currentFunction.emitInstruction(OP_POP) # Clean up stack

			self.compileExpression(sequence[-1], isTailCall)


	#
	# Of the form (let ((variable value) (variable value) (variable value)...) expr)
	# Reserve local variables and assign initial values to them.
	#
	def compileLet(self, expr, isTailCall=False):
		# Reserve space on stack for local variables
		variableCount = len(expr[1])
		self.currentFunction.enterScope()

		# Walk through each variable, define in scope, evaluate the initial value,
		# and assign.
		for variable, value in expr[1]:
			symbol = self.currentFunction.reserveLocalVariable(variable)
			self.compileExpression(value)
			self.currentFunction.emitInstruction(OP_SETLOCAL, symbol.index)
			self.currentFunction.emitInstruction(OP_POP) # setlocal leaves on stack, remove it

		# Now evaluate the predicate, which can be a sequence
		self.compileSequence(expr[2:], isTailCall)
		self.currentFunction.exitScope()

	def performGlobalFixups(self):
		# Check if there are uninitialized globals
		for varName in self.globals:
			if not self.globals[varName].initialized:
				print 'unknown variable %s' % varName

		for function, functionOffset, target in self.globalFixups:
			if isinstance(target, Function):
				function.patch(functionOffset, target.baseAddress)
			elif isinstance(target, Symbol):
				if target.type == Symbol.GLOBAL_VARIABLE:
					function.patch(functionOffset, target.index)
				elif target.type == Symbol.FUNCTION:
					function.patch(functionOffset, target.function.baseAddress)
			else:
				raise Exception('unknown global fixup type')

OPTIMIZE_BINOPS = {
	'+' : (lambda x, y : x + y),
	'-' : (lambda x, y : x - y),
	'/' : (lambda x, y : x / y),
	'*' : (lambda x, y : x * y),
	'bitwise-and' : (lambda x, y : x & y),
	'bitwise-or' : (lambda x, y : x | y),
	'bitwise-xor' : (lambda x, y : x ^ y),
	'lshift' : (lambda x, y : x << y),
	'rshift' : (lambda x, y : x >> y),
	'>' : (lambda x, y : 1 if x > y else 0),
	'>=' : (lambda x, y : 1 if x >= y else 0),
	'<' : (lambda x, y : 1 if x < y else 0),
	'<=' : (lambda x, y : 1 if x <= y else 0),
	'=' : (lambda x, y : 1 if x == y else 0),
	'<>' : (lambda x, y : 1 if x != y else 0)
}

OPTIMIZE_UOPS = {
	'bitwise-not' : (lambda x : ~x),
	'-' : (lambda x : -x),
	'not' : (lambda x : 1 if x else 0)
}

def isPowerOfTwo(x):
	return (x & (x - 1)) == 0

def makeLegalConstant(x):
	x &= 0xFFFF
	if x & 0x8000:
		return -((~x + 1) & 0xffff)
	else:
		return x
#
# Simple arithmetic constant folding on the S-Expression data structure.
#
def optimize(expr):
	if isinstance(expr, list) and len(expr) > 0:
		if expr[0] == 'quote':
			return expr			# Copy everything unaltered in quotes
		else:
			# Fold arithmetic expressions if possible
			optimizedParams = [ optimize(sub) for sub in expr[1:] ]
			if not isinstance(expr[0], list) and expr[0] in OPTIMIZE_BINOPS \
				and len(expr) == 3 and isinstance(optimizedParams[0], int) \
				and isinstance(optimizedParams[1], int):
				return makeLegalConstant(OPTIMIZE_BINOPS[expr[0]](optimizedParams[0], optimizedParams[1]))
			
			if not isinstance(expr[0], list) and expr[0] in OPTIMIZE_UOPS \
				and len(expr) == 2 and isinstance(optimizedParams[0], int):
				return makeLegalConstant(OPTIMIZE_UOPS[expr[0]](optimizedParams[0]))
				
			# Short circuit.  If any parameters are constant 0, the whole thing is zero
			if expr[0] == 'and':
				allOnes = True
				for param in optimizedParams:
					if isinstance(param, int):
						if param == 0:
							return 0
					else:
						allOnes = False

				if allOnes:
					return 1

				# Could not optimize
				return [ expr[0] ] + optimizedParams				
			
			# Short circuit.  If any parameters are constant 1, the whole thing is 1
			if expr[0] == 'or':
				allZeroes = True
				for param in optimizedParams:
					if isinstance(param, int):
						if param != 0:
							return 1
					else:
						allZeroes = False

				if allZeroes:
					return 0

				# Could not optimize
				return [ expr[0] ] + optimizedParams				
				
			# If a conditional form has a constant expression, include only the
			# appropriate clause
			if not isinstance(expr[0], list) and expr[0] == 'if' \
				and isinstance(optimizedParams[0], int):
				if optimizedParams[0] != 0:
					return optimizedParams[1]
				elif len(optimizedParams) > 2:
					return optimizedParams[2]
				else:
					return 0	# Did not have an else, this is an empty expression

			# Strength reduction for power of two multiplies and divides
			if len(optimizedParams) > 1 and isinstance(optimizedParams[1], int) \
				and isPowerOfTwo(optimizedParams[1]) and optimizedParams[1] > 0  \
				and (expr[0] == '*' or expr[0] == '/'):
				return [ 'lshift' if expr[0] == '*' else 'rshift', optimizedParams[0], 
					int(math.log(int(optimizedParams[1]), 2)) ]

			# Nothing to optimize, return the expression as is
			return [ expr[0] ] + optimizedParams
	else:
		return expr

#
# For debugging
#

# name, hasparam
disasmTable = {
	OP_NOP			: ('nop', False),
	OP_CALL 		: ('call', False),
	OP_RETURN 		: ('return', False),
	OP_CLEANUP 		: ('cleanup', True),
	OP_POP 			: ('pop', False),
	OP_ADD 			: ('add', False),
	OP_SUB 			: ('sub', False),
	OP_PUSH 		: ('push', True),
	OP_LOAD 		: ('load', False),
	OP_STORE 		: ('store', False),
	OP_GETLOCAL 	: ('getlocal', True),
	OP_SETLOCAL 	: ('setlocal', True),
	OP_GOTO 		: ('goto', True),
	OP_BFALSE 		: ('bfalse', True),
	OP_REST 		: ('rest', False),
	OP_RESERVE 		: ('reserve', True),
	OP_GTR			: ('gtr', False),
	OP_GTE			: ('gte', False),
	OP_EQ			: ('eq', False),
	OP_NEQ			: ('neq', False),
	OP_DUP			: ('dup', False),
	OP_GETTAG		: ('gettag', False),
	OP_SETTAG		: ('settag', False),
	OP_AND			: ('and', False),
	OP_OR			: ('or', False),
	OP_XOR			: ('xor', False),
	OP_LSHIFT		: ('lshift', False),
	OP_RSHIFT		: ('rshift', False),
	OP_GETBP		: ('getbp', False)
}

def disassemble(outfile, instructions, baseAddress):
	for pc, word in enumerate(instructions):
		outfile.write('' + str(baseAddress + pc))
		opcode = (word >> 16)
		name, hasParam = disasmTable[opcode]
		if hasParam:
			paramValue = word & 0xffff
			if paramValue & 0x8000:
				paramValue = -(((paramValue ^ 0xffff) + 1) & 0xffff)

			outfile.write('\t' + name + ' ' + str(paramValue) + '\n')
		else:
			outfile.write('\t' + name + '\n')

def prettyPrintSExpr(listfile, expr, indent = 0):
	if isinstance(expr, list):
		if len(expr) > 0 and expr[0] == 'function':
			listfile.write('\n')

		listfile.write('\n')
		for x in range(indent):
			listfile.write('  ')
		
		listfile.write('(')
		for elem in expr:
			if elem != expr[0]:
				listfile.write(' ')

			prettyPrintSExpr(listfile, elem, indent + 1)
			
		listfile.write(')')
	else:
		listfile.write(str(expr))


#
# The macro processor is actually a small lisp interpreter
# When we see macro, we evaluate its expression with the arguments as parameters.
#
class MacroProcessor:
	def __init__(self):
		self.macroList = {}

	def expandBackquote(self, expr, env):
		if isinstance(expr, list):
			if expr[0] == 'unquote':
				return self.eval(expr[1], env)	# This gets evaluated regularly
			else:
				return [ self.expandBackquote(term, env) for term in expr ]
		else:
			return expr

	def eval(self, expr, env):
		if isinstance(expr, list):
			func = expr[0]
			if func == 'first':
				return self.eval(expr[1], env)[0]
			elif func == 'rest':
				return self.eval(expr[1], env)[1]
			elif func == 'if':		# (if test trueexpr falsexpr)
				if self.eval(expr[1], env):
					return self.eval(pair[2], env)
				elif len(expr) > 3:
					return self.eval(expr[3], env)
				else:
					return 0
			elif func == 'assign':	# (assign var value)
				env[expr[1]] = self.eval(expr[2], env)
			elif func == 'list':
				return [ self.eval(element, env) for element in expr[1:] ]
			elif func == 'quote':
				return expr[1]
			elif func == 'backquote':
				return self.expandBackquote(expr[1], env)
			elif func == 'cons':
				return [ self.eval(expr[1], env) ] + [ self.eval(expr[2], env) ]
			elif func in OPTIMIZE_BINOPS:
				return OPTIMIZE_BINOPS[func](self.eval(expr[1], env), self.eval(expr[2], env) )
			elif func in self.macroList:
				# Invoke a sub-macro
				newEnv = copy.copy(env)
				argList, body = self.macroList[expr[0]]		
				for name, value in zip(argList, expr[1:]):
					newEnv[name] = value
					
				return self.eval(body, newEnv)
			else:
				# Call a function. This is a little tricky, since we can't really
				# define functions. Stubbed out for now.
				func = self.env[expr[0]]		# Func is (function (arg arg arg) body)
				if func == None or not isinstance(func, list) or func[0] != 'function':
					raise Exception('bad function call during macro expansion', '')
				for name, value in zip(func[0], expr[1:]):
					env[name] = self.eval(value, env)

				return self.eval(func[1], env)
		elif isinstance(expr, int):
			return expr	
		else:
			return env[expr]

	def macroPreProcess(self, program):
		updatedProgram = []
		for statement in program:
			if isinstance(statement, list) and statement[0] == 'defmacro':
				# (defmacro <name> (arg list) replace)
				self.macroList[statement[1]] = (statement[2], statement[3])
			else:
				updatedProgram += [ self.macroExpandRecursive(statement) ]

		return updatedProgram

	def macroExpandRecursive(self, statement):
		if isinstance(statement, list) and len(statement) > 0:
			if not isinstance(statement[0], list) and statement[0] in self.macroList:
				# This is a macro form.  Evalute the macro now and replace this form with
				# the result.
				argNames, body = self.macroList[statement[0]]
				if len(argNames) != len(statement) - 1:
					print 'warning: macro expansion of %s has the wrong number of arguments' % statement[0]
					print 'expected %d got %d:' % (len(argNames), len(statement) - 1)
					for arg in statement[1:]:
						print arg

				env = {}
				for name, value in zip(argNames, statement[1:]):
					env[name] = self.macroExpandRecursive(value)
					
				return self.eval(body, env)
			else:
				return [ self.macroExpandRecursive(term) for term in statement ]
		else:
			return statement

parser = Parser()
parser.parseFile('runtime.lisp')
for filename in sys.argv[1:]:
	parser.parseFile(filename)

macro = MacroProcessor()
expanded = macro.macroPreProcess(parser.getProgram())

optimized = [ optimize(sub) for sub in expanded ]

compiler = Compiler()
code = compiler.compile(optimized)

outfile = open('program.hex', 'w')
for instr in code:
	outfile.write('%06x\n' % instr)
		
outfile.close()
