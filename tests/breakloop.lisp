; 
; Copyright 2011-2012 Jeff Bush
; 
; Licensed under the Apache License, Version 2.0 (the "License");
; you may not use this file except in compliance with the License.
; You may obtain a copy of the License at
; 
;     http://www.apache.org/licenses/LICENSE-2.0
; 
; Unless required by applicable law or agreed to in writing, software
; distributed under the License is distributed on an "AS IS" BASIS,
; WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
; See the License for the specific language governing permissions and
; limitations under the License.
; 

(print
	(while (< j 10)
		(print j)
		(if (= j 7)
			(break 37))

		(assign j (+ j 1))))

; CHECK: 0
; CHECK: 1
; CHECK: 2
; CHECK: 3
; CHECK: 4
; CHECK: 5
; CHECK: 6
; CHECK: 7
; CHECK: 37