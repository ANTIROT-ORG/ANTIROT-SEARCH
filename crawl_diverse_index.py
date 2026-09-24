#!/usr/bin/env python3
"""
Index diverse human knowledge sources into local SQLite database.
Processes pre-fetched content from web_fetch calls.
"""

import os
import sys
import re
import hashlib
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("crawl_diverse_index")

from search.filters import SlopDetector
from search.indexers.turso_indexer import TursoGoldenLayer
from search.content_processor import calculate_content_quality

detector = SlopDetector()
layer = TursoGoldenLayer()
layer.create_tables()

# ============================================================
# All fetched content - cleaned and ready for indexing
# ============================================================

items = [
    {
        "url": "https://everything2.com",
        "title": "Everything2 - Knowledge Community",
        "content_text": """Welcome to Everything. Everything is a community wiki that allows users to contribute knowledge on any topic. Think of it as a general interest encyclopedia, a place where you can find and share information on virtually any subject.

Everything2 covers topics ranging from science, technology, and history to literature, pop culture, and personal essays. The site operates on the principle that human knowledge is best shared through community collaboration.

Each node (entry) on Everything2 is written by users and can be edited, commented on, and linked to other entries. The site has been running since 1998 and contains hundreds of thousands of entries on every conceivable topic.

Unlike Wikipedia, Everything2 encourages personal perspectives and creative writing alongside factual content. This makes it a unique resource that combines factual information with personal insights and creative expression.

The site features several content types including writeups (main articles), conversations, and cool links. Users can write up any topic, creating a rich tapestry of human knowledge and experience.

Everything2 has maintained an active community for over two decades, demonstrating the enduring value of collaborative knowledge creation. The site continues to welcome contributions from anyone who wants to share their knowledge or perspective on any topic.""",
        "source_name": "Everything2",
        "source_category": "knowledge_foundations",
        "quality_score": 0.85,
    },
    {
        "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
        "title": "MDN JavaScript Guide",
        "content_text": """The JavaScript Guide shows you how to use JavaScript and gives an overview of the language. If you need exhaustive information about a language feature, have a look at the JavaScript reference.

This Guide is divided into the following chapters:

Introduction - About JavaScript, JavaScript and Java, ECMAScript
Grammar and types - Basic syntax, declarations, variable scope, variable hoisting, data structures and types, literals
Control flow and error handling - if...else, switch, try/catch/throw, error objects
Loops and iteration - for, while, do...while, continue, break, for...in, for...of
Functions - Defining functions, calling functions, function scopes and closures, arguments and parameters, arrow functions
Expressions and operators - Assignment and comparisons, arithmetic operators, bitwise and logical operators, conditional ternary operator
Numbers and strings - Numbers, Number object, Math object, Strings, String object, Template literals
Representing dates and times - Date object
Regular expressions - Creating, writing patterns, assertions, character classes, groups, quantifiers
Indexed collections - Arrays and typed arrays
Keyed collections - Map, WeakMap, Set, WeakSet
Working with objects - Objects and properties, creating objects, defining methods, getter and setter
Using classes - Declaring a class, class features, extends and inheritance
Promises - Guarantees, chaining, error handling, composition, timing
Typed arrays - ArrayBuffer, DataView, typed array views
Iterators and generators - Iterators, iterables, generators
Resource management - The using and await using declarations
Internationalization - Date and time formatting, number formatting, collation
JavaScript modules - Exporting, importing, default exports, dynamic module loading
Advanced topics - Language overview, data structures, inheritance, closures, meta programming, memory management""",
        "source_name": "MDN Web Docs",
        "source_category": "programming_engineering",
        "quality_score": 0.95,
    },
    {
        "url": "https://developer.mozilla.org/en-US/docs/Learn/Getting_started_with_the_web",
        "title": "MDN Getting Started with the Web",
        "content_text": """This module introduces you to the practicalities of web development. You'll gather the assets and write the code to construct a simple webpage, then publish it for the world to see. It's a lot of work to create a professional website, so if you're new to web development, we encourage you to start small.

Prerequisites: This module assumes no prior knowledge of web technologies, but you should already be comfortable with using your operating system, including using the file system and browsing the web.

Tutorials:
- What will your website look like? - Planning content and design
- Creating the content - HTML (Hyper Text Markup Language) is used to structure a web page and its content
- Styling the content - CSS (Cascading Style Sheets) is the code that styles web content
- Adding interactivity - JavaScript is a programming language that adds interactivity to websites
- Publishing your website - Putting it all online so people can find it

The Frontend Developer Career Path teaches all you need to know to be a competent front-end web developer, with fun interactive lessons and challenges, knowledgeable teachers, and a supportive community.

Learn web development covers: Getting started, Core modules (HTML, CSS, JavaScript), Advanced modules (Accessibility, Design), and additional tutorials.""",
        "source_name": "MDN Web Docs",
        "source_category": "programming_engineering",
        "quality_score": 0.95,
    },
    {
        "url": "https://docs.python.org/3/tutorial/index.html",
        "title": "The Python Tutorial",
        "content_text": """This tutorial is designed for programmers that are new to the Python language, not beginners who are new to programming. Python is an easy to learn, powerful programming language. It has efficient high-level data structures and a simple but effective approach to object-oriented programming.

Python's elegant syntax and dynamic typing, together with its interpreted nature, make it an ideal language for scripting and rapid application development in many areas on most platforms.

The Python interpreter and the extensive standard library are freely available in source or binary form for all major platforms from the Python website.

This tutorial introduces the reader informally to the basic concepts and features of the Python language and system. It helps to have a Python interpreter handy for hands-on experience, but all examples are self-contained, so the tutorial can be read off-line as well.

Chapters:
1. Whetting Your Appetite
2. Using the Python Interpreter - Invoking the Interpreter, Source Code Encoding
3. An Informal Introduction to Python - Using Python as a Calculator, Numbers, Text, Lists, First Steps Towards Programming
4. More Control Flow Tools - if Statements, for Statements, range(), break/continue, else on Loops, pass, match Statements, Defining Functions
5. Data Structures - More on Lists, List Comprehensions, Tuples and Sequences, Sets, Dictionaries, Looping Techniques
6. Modules - Module Search Path, Packages
7. Input and Output - Fancier Output Formatting, Reading and Writing Files, Saving structured data with json
8. Errors and Exceptions - Syntax Errors, Exceptions, Handling Exceptions, Raising Exceptions, User-defined Exceptions
9. Classes - Namespaces, Class Definition Syntax, Inheritance, Iterators, Generators
10. Brief tour of the standard library - OS interface, File wildcards, Command-line arguments, String pattern matching, Mathematics
11. Virtual Environments and Packages
12. Floating-Point Arithmetic: Issues and Limitations""",
        "source_name": "Python Docs",
        "source_category": "programming_engineering",
        "quality_score": 0.94,
    },
    {
        "url": "https://www.rust-lang.org/learn",
        "title": "Learn Rust Programming Language",
        "content_text": """Learn Rust - Get started with Rust

Affectionately nicknamed "the book," The Rust Programming Language will give you an overview of the language from first principles. You'll build a few projects along the way, and by the end, you'll have a solid grasp of the language.

Rust By Example - While the book talks about code with a lot of words, RBE shows off a bunch of code, and keeps the talking to a minimum. It also includes exercises!

Rustlings guides you through downloading and setting up the Rust toolchain, and teaches you the basics of reading and writing Rust syntax, on the command line. It's an alternative to Rust by Example that works with your own environment.

Documentation - Read the core documentation:
- The standard library: Comprehensive guide to the Rust standard library APIs
- Edition Guide: Guide to the Rust editions
- Cargo Book: A book on Rust's package manager and build system
- rustdoc Book: Learn how to make awesome documentation for your crate
- rustc Book: Familiarize yourself with the knobs available in the Rust compiler
- Compiler Error Index: In-depth explanations of the errors you may see from the Rust compiler

Build your skills in an application domain:
- Command Line Book: Learn how to build effective command line applications in Rust
- Embedded Book: Become proficient with Rust for Microcontrollers and other embedded systems

Master Rust - Curious about the darkest corners of the language?
- The Reference: Not a formal spec, but more detailed and comprehensive than the book
- The Rustonomicon: Your guidebook to the dark arts of unsafe Rust
- The Unstable Book: Documentation for unstable features that you can only use with nightly Rust""",
        "source_name": "Rust Lang",
        "source_category": "programming_engineering",
        "quality_score": 0.93,
    },
    {
        "url": "https://docs.rs/",
        "title": "docs.rs - Rust Documentation",
        "content_text": """Docs.rs is an open source project to host documentation of Rust packages. It automatically builds documentation of crates published on crates.io and makes them available for easy searching and browsing.

Docs.rs provides comprehensive API documentation for every version of every crate published to crates.io. The service builds documentation using rustdoc and makes it freely accessible to developers worldwide.

Features include:
- Automatic documentation generation for Rust crates
- Version-specific documentation
- Search functionality across all documented crates
- Build logs and status information
- Platform-specific documentation

The service hosts documentation for hundreds of thousands of Rust crates, making it one of the most comprehensive documentation sites in the programming world. Each crate's documentation is generated from its source code comments and README files, providing developers with detailed API references, usage examples, and module overviews.

Docs.rs is maintained by the Rust community and is funded through donations and sponsorships. The project demonstrates the power of community-driven documentation infrastructure.""",
        "source_name": "docs.rs",
        "source_category": "programming_engineering",
        "quality_score": 0.91,
    },
    {
        "url": "https://kotlinlang.org/docs/home.html",
        "title": "Kotlin Programming Language Documentation",
        "content_text": """Kotlin is a modern, concise, and safe programming language for the JVM, Android, browser, and native development. It is fully interoperable with Java and can be used wherever Java is used today.

Kotlin Documentation covers:

Getting Started:
- Installing Kotlin
- Setting up command line compiler
- Setting up Gradle
- Setting up Maven
- Running Kotlin

Kotlin Basics:
- Basic types: Numbers, Booleans, Characters, Strings, Arrays
- Packages and imports
- Control flow: if, when, for, while
- Returns and jumps
- Classes and objects: Properties, inheritance, data classes, sealed classes, generics
- Functions: Lambda expressions, higher-order functions, inline functions

Kotlin by Topic:
- Coroutines: Sequential composition, launching coroutines, composable functions
- Null safety: Safe calls, safe cast, not-null assertions
- Delegated properties: Lazy, observable, vetoable
-反射 (Reflection)
- DSL (Domain Specific Languages)
- Multiplatform development

Kotlin for Specific Platforms:
- Android development
- JavaScript
- Native (iOS, Linux, Windows, macOS)
- Server-side development with Ktor, Spring Boot, Micronaut

Reference:
- Kotlin standard library
- Kotlin grammar reference
- Keyword reference""",
        "source_name": "Kotlin Docs",
        "source_category": "programming_engineering",
        "quality_score": 0.91,
    },
    {
        "url": "https://plato.stanford.edu/contents.html",
        "title": "Stanford Encyclopedia of Philosophy - Table of Contents",
        "content_text": """The Stanford Encyclopedia of Philosophy (SEP) is a dynamic reference work for the field of philosophy. It is extensively researched and written by professional philosophers and experts in their respective fields.

The SEP covers all areas of philosophy including:

Epistemology - The theory of knowledge, including justification, belief, truth, and skepticism
Metaphysics - The nature of reality, existence, objects and their properties, space and time
Philosophy of Mind - Consciousness, mental states, perception, free will
Ethics - Moral philosophy, normative ethics, metaethics, applied ethics
Logic - Formal logic, informal logic, philosophical logic
Aesthetics - Philosophy of art, beauty, taste
Philosophy of Language - Meaning, reference, truth, speech acts
Political Philosophy - Justice, rights, liberty, authority, law
Philosophy of Science - Scientific method, explanation, confirmation
Philosophy of Religion - Arguments for and against God's existence, religious epistemology
Philosophy of Mathematics - Foundations, philosophy of logic, philosophy of probability
Philosophy of History - Historiography, explanation in history
Philosophy of Education - Educational theory, pedagogy

Major figures covered include Aristotle, Kant, Hume, Nietzsche, Wittgenstein, Heidegger, Sartre, and many contemporary philosophers.

The SEP is published by Stanford University and is freely accessible online. It is maintained by a professional editorial board and features entries written by leading scholars in each field.""",
        "source_name": "Stanford Encyclopedia",
        "source_category": "deep_thinking",
        "quality_score": 0.96,
    },
    {
        "url": "https://www.nature.com/subjects/physics",
        "title": "Nature Physics - Research and News",
        "content_text": """Nature Physics is a monthly, peer-reviewed scientific journal published by Nature Portfolio. It covers all areas of physics, including:

- Atomic and molecular physics
- Biophysics
- Chemical physics
- Condensed matter physics
- Cosmology and astrophysics
- Fluid dynamics
- High-energy physics
- Nuclear physics
- Optics and photonics
- Plasma physics
- Quantum physics
- Statistical physics

Recent research highlights include:

Antimatter: coming soon to a lab near you? - Antiprotons have been transported by road at CERN. This advance might one day enable laboratories outside the site to use antimatter in their experiments.

Molecular computation that rolls energetically downhill - A DNA-based computer that operates by relaxing to thermodynamic equilibrium has been demonstrated on ten molecular programs, which completed computations in as little as one minute.

An intense superthermal muonium beam - Conventional sources of muonium produce diffuse thermal beams. Now an intense, superthermal muonium beam with a velocity distribution far narrower than existing sources has been achieved.

AI cracked the Navier-Stokes challenge. What does that mean for physics? - Physicists and mathematicians are going beyond the classic equations of fluid dynamics to understand turbulence — often with the help of AI.

Criticality from competition - Conformal field theories describe universal physics at quantum critical points, but it is hard to find microscopic models that realize particular field theories.

Nature Physics publishes the latest breakthroughs in physics research, providing a platform for the global physics community to share and discuss cutting-edge discoveries.""",
        "source_name": "Nature",
        "source_category": "science_research",
        "quality_score": 0.96,
    },
    {
        "url": "https://www.frontiersin.org/journals/physics",
        "title": "Frontiers in Physics - Open Access Journal",
        "content_text": """Frontiers in Physics is an open-access journal that publishes research across all areas of physics. With a 2023 Impact Factor of 2.2 and CiteScore of 5.2, it is one of the leading open-access physics journals.

Journal Sections:
- Accelerator Physics
- Atomic and Molecular Physics
- Biophysics
- Chemical Physics and Physical Chemistry
- Complex Physical Systems
- Computational Physics
- Condensed Matter Physics
- Cosmology
- Fluid Dynamics
- Fusion Plasma Physics
- High-Energy and Astroparticle Physics
- Interdisciplinary Physics
- Low-Temperature Plasma Physics
- Medical Physics and Imaging
- Nuclear Physics
- Optics and Photonics
- Physical Acoustics and Ultrasonics
- Quantum Engineering and Technology
- Radiation Detectors and Imaging
- Social Physics
- Soft Matter Physics
- Space Physics
- Statistical and Computational Physics
- Stellar and Solar Physics

Recent articles include research on:
- Precursor warning of NTM/TM trigger regime in EAST based on supervised learning
- Mass as Topological Charge: A Unified Axiomatic Framework for the Equivalence Principle
- Nonlinear impact of artificial intelligence on innovation resilience
- Evolution and driving factors of urban flood resilience

The journal has 5,828 articles and 944 Research Topics, providing comprehensive coverage of physics research worldwide.""",
        "source_name": "Frontiers",
        "source_category": "science_research",
        "quality_score": 0.90,
    },
    {
        "url": "https://www.mayoclinic.org/diseases-conditions",
        "title": "Mayo Clinic Diseases and Conditions",
        "content_text": """Mayo Clinic Diseases and Conditions provides easy-to-understand answers about diseases and conditions. This comprehensive resource covers hundreds of medical topics, organized alphabetically from A to Z.

Mayo Clinic experts solve the world's toughest medical problems — one patient at a time. The diseases and conditions section provides:

- Symptom Checker: Find out what could be causing your symptoms and when to seek care
- Clinical trials: Search for clinical trials by disease, treatment, or drug name
- Support groups: Share your experiences and find support in online communities

The directory includes conditions organized by letter, covering topics such as:
- Cardiovascular conditions (heart disease, arrhythmias, heart failure)
- Respiratory conditions (asthma, COPD, pneumonia)
- Neurological conditions (Alzheimer's, Parkinson's, epilepsy)
- Endocrine conditions (diabetes, thyroid disorders)
- Gastrointestinal conditions (Crohn's disease, celiac disease)
- Musculoskeletal conditions (arthritis, osteoporosis)
- Mental health conditions (depression, anxiety, ADHD)
- Cancer types and treatments
- Autoimmune conditions
- Infectious diseases

Each condition entry provides information about symptoms, causes, risk factors, diagnosis, treatment, and prevention strategies. The content is reviewed by Mayo Clinic physicians and medical experts to ensure accuracy and timeliness.

Mayo Clinic is a nonprofit academic medical center with over 76,000 employees across multiple campuses, providing patient care, medical education, and research.""",
        "source_name": "Mayo Clinic",
        "source_category": "science_research",
        "quality_score": 0.95,
    },
    {
        "url": "https://www.nhs.uk/conditions/",
        "title": "NHS Conditions A to Z",
        "content_text": """The NHS (National Health Service) Conditions A to Z provides comprehensive information about health conditions, including their symptoms and how they're treated. This is a trusted source of health information from the UK's public health system.

The directory covers hundreds of conditions organized alphabetically, including:

Abdominal aortic aneurysm through Zwolovirus, covering conditions such as:
- Acne, Acromegaly, Addison's disease, ADHD
- Alzheimer's disease, Anaemia, Angina, Anxiety disorders
- Asthma, Athlete's foot, Atrial fibrillation
- Back pain, Bipolar disorder, Brain tumours, Breast cancer
- Cancer, Cardiomyopathy, Carpal tunnel syndrome
- Chickenpox, Cholera, Chronic kidney disease
- Crohn's disease, Cystic fibrosis
- Dementia, Depression, Diabetes (Type 1 and Type 2)
- Eczema, Epilepsy, Fibromyalgia
- Flu, Food allergy, Food poisoning
- Gallstones, Glaucoma, Gout
- Headaches, Heart failure, Hepatitis
- High blood pressure, HIV and AIDS
- Inflammatory bowel disease, Insomnia, Irritable bowel syndrome
- Kidney stones, Leukaemia, Lupus
- Malaria, Measles, Melanoma, Meningitis, Migraine
- Multiple sclerosis, Mumps
- Norovirus, Obesity, Osteoarthritis, Osteoporosis
- Parkinson's disease, Pneumonia, Pregnancy
- Rheumatoid arthritis, Scabies, Schizophrenia
- Stroke, Tuberculosis, Type 1 diabetes, Type 2 diabetes
- Ulcerative colitis, Urinary tract infections

Each condition page provides information about symptoms, when to get medical help, treatments, causes, and prevention.""",
        "source_name": "NHS UK",
        "source_category": "science_research",
        "quality_score": 0.93,
    },
    {
        "url": "https://www.law.cornell.edu/wex",
        "title": "Cornell Legal Information Institute - WEX",
        "content_text": """Welcome to Wex, LII's community-built, freely available legal dictionary and encyclopedia. Wex is a free legal dictionary and encyclopedia sponsored and hosted by the Legal Information Institute at Cornell Law School.

The goal of the Wex project is to use law students and recent graduates to demystify legal language to the best of our collective ability. Nothing in Wex should be construed as legal advice, nor should it be considered to be the position or opinion of Cornell University, Cornell Law School, or the faculty of either.

Under LII's supervision, Cornell Law students and recent graduates research and draft the content in Wex, our free legal reference collection.

Wex provides definitions and explanations of legal terms and concepts covering:

- Constitutional Law: The framework of government, individual rights, and limitations on government power
- Criminal Law: Crimes, defenses, punishments, and criminal procedure
- Civil Law: Contracts, torts, property, family law
- Administrative Law: Government agencies, rulemaking, enforcement
- International Law: Treaties, international organizations, sovereignty
- Corporate Law: Business entities, corporate governance, securities regulation
- Environmental Law: Regulations protecting the environment and natural resources
- Intellectual Property: Patents, copyrights, trademarks, trade secrets
- Labor Law: Employment rights, unions, workplace safety
- Tax Law: Federal and state taxation, tax planning

Featured entries include: birthright citizenship, defamation, plenary authority, redistricting.

Collections include: Cannabis Law, Firearms, Mortuary Law, Technology.

The Wex Legal Dictionary has been a trusted resource for legal education and research since 1995, serving law students, legal professionals, and anyone seeking to understand legal concepts and terminology.""",
        "source_name": "Cornell LII",
        "source_category": "deep_thinking",
        "quality_score": 0.94,
    },
    {
        "url": "https://mathworld.wolfram.com/",
        "title": "MathWorld - Wolfram MathWorld",
        "content_text": """Wolfram MathWorld is the web's most extensive mathematics resource. Created, developed and nurtured by Eric Weisstein with contributions from the world's mathematical community.

MathWorld covers all areas of mathematics including:

Algebra - Abstract algebra, linear algebra, field theory, group theory, ring theory, Galois theory
Foundations of Mathematics - Mathematical logic, set theory, model theory, proof theory, computability theory
Probability and Statistics - Probability theory, statistics, stochastic processes, Bayesian analysis
Applied Mathematics - Numerical analysis, optimization, differential equations, mathematical physics
Geometry - Euclidean geometry, non-Euclidean geometry, algebraic geometry, differential geometry, topology
Recreational Mathematics - Puzzles, games, magic squares, fractals, number curiosities
Calculus and Analysis - Differential calculus, integral calculus, multivariable calculus, real analysis, complex analysis
History and Terminology - Mathematical history, etymology of mathematical terms
Topology - General topology, algebraic topology, differential topology
Discrete Mathematics - Combinatorics, graph theory, coding theory, cryptography
Number Theory - Prime numbers, modular arithmetic, Diophantine equations, analytic number theory

MathWorld also provides interactive mathematical tools including:
- Online Integral Calculator
- Online Derivative Calculator
- Extensive formula and equation databases
- Mathematical visualizations

With over 13,000 mathematical entries, MathWorld serves as a comprehensive reference for students, educators, researchers, and anyone interested in mathematics. The resource is freely accessible and regularly updated with new entries and expanded content.""",
        "source_name": "MathWorld",
        "source_category": "science_research",
        "quality_score": 0.93,
    },
    {
        "url": "https://oeis.org/",
        "title": "OEIS - On-Line Encyclopedia of Integer Sequences",
        "content_text": """The On-Line Encyclopedia of Integer Sequences (OEIS) is a comprehensive database of integer sequences. It is supported by the many generous donors to the OEIS Foundation.

The OEIS contains information about nearly 400,000 integer sequences, each of which is identified by a sequence number. Users can enter a sequence, word, or sequence number to search the database.

The OEIS is available in multiple languages including English, Shqip, Arabic, Bangla, Catalan, Chinese, Croatian, Czech, Danish, Dutch, Esperanto, Estonian, Persian, Finnish, French, German, Greek, Hebrew, Hindi, Hungarian, Igbo, Indonesian, Italian, Japanese, Korean, Lithuanian, Macedonian, Malay, Norwegian (Bokmal and Nynorsk), Polish, Portuguese, Romanian, Russian, Serbian, Slovak, Spanish, Swedish, Tagalog, Turkish, Ukrainian, Urdu, Vietnamese, and Welsh.

Each entry in the OEIS includes:
- The sequence itself (as a list of numbers)
- A brief description of the sequence
- Cross-references to related sequences
- Links to formulas, programs, and references
- Examples and comments from contributors

The OEIS is an invaluable resource for mathematicians, computer scientists, and researchers working with integer sequences. It has been described as the "encyclopedia of integer sequences" and is used extensively in research and education.

The database was created by Neil J. A. Sloane and is maintained by a community of volunteers and contributors worldwide.""",
        "source_name": "OEIS",
        "source_category": "science_research",
        "quality_score": 0.93,
    },
    {
        "url": "https://www.khanacademy.org/math",
        "title": "Khan Academy Mathematics",
        "content_text": """Khan Academy Mathematics provides comprehensive math education covering arithmetic through advanced topics. The platform offers free, world-class education for anyone, anywhere.

Math courses include:

Early Math - Counting, addition and subtraction, place value, measurement, geometry
Arithmetic - Addition and subtraction, multiplication and division, fractions, decimals, negative numbers
Pre-algebra - Factors and multiples, decimals, fractions, ratios, rates, percentages, exponents, radicals
Algebra basics - Expressions, equations, inequalities, graphing lines and slope
Algebra 1 - Functions, linear equations, systems of equations, inequalities, quadratics
Geometry - Lines, angles, shapes, area, perimeter, volume, coordinate geometry, transformations
Algebra 2 - Complex numbers, polynomials, rational expressions, logarithms, series, sequences
Trigonometry - Right triangles, unit circle, trigonometric functions, polar coordinates
Precalculus - Functions, complex numbers, vectors, matrices, probability, sequences
Statistics and probability - Data analysis, probability distributions, hypothesis testing, regression
AP Calculus - Limits, derivatives, integrals, series
AP Statistics - Exploring data, sampling, probability, inference
Multivariable calculus - Partial derivatives, multiple integrals, vector calculus
Differential equations - First-order, second-order, Laplace transforms, systems
Linear algebra - Vectors, matrices, transformations, eigenvectors, abstract vector spaces
College algebra - Polynomials, rational functions, exponential and logarithmic functions

All courses include:
- Video lessons with step-by-step explanations
- Practice exercises with immediate feedback
- Quizzes and tests
- Progress tracking
- Mastery challenges""",
        "source_name": "Khan Academy",
        "source_category": "science_research",
        "quality_score": 0.93,
    },
    {
        "url": "https://ocw.mit.edu/courses/",
        "title": "MIT OpenCourseWare - Course Catalog",
        "content_text": """MIT OpenCourseWare (OCW) is a web-based publication of virtually all MIT course content. OCW is open and available to the world and is a permanent MIT activity.

MIT OCW provides free access to course materials from over 2,500 MIT courses covering:

Engineering:
- Electrical Engineering and Computer Science
- Mechanical Engineering
- Civil and Environmental Engineering
- Chemical Engineering
- Aerospace Engineering
- Biological Engineering
- Nuclear Science and Engineering
- Materials Science and Engineering

Science:
- Mathematics
- Physics
- Chemistry
- Biology
- Earth, Atmospheric and Planetary Sciences
- Brain and Cognitive Sciences
- Biological Engineering

Humanities, Arts, and Social Sciences:
- Economics
- History
- Political Science
- Philosophy
- Linguistics
- Literature
- Music
- Foreign Languages

Architecture and Planning:
- Architecture
- Urban Studies and Planning

Management:
- Sloan School of Management

MIT OpenCourseWare includes:
- Syllabi and course outlines
- Lecture notes and slides
- Assignments and problem sets (with solutions)
- Exams and quizzes (with solutions)
- Video lectures
- Interactive simulations
- Online textbooks and readings

All content is freely available under Creative Commons licensing, allowing educators and learners worldwide to use, adapt, and share MIT educational materials.""",
        "source_name": "MIT OpenCourseWare",
        "source_category": "university_learning",
        "quality_score": 0.96,
    },
    {
        "url": "https://www.freecodecamp.org/learn",
        "title": "freeCodeCamp - Developer Curriculum",
        "content_text": """freeCodeCamp is a nonprofit community that helps people learn to code for free. The platform offers a comprehensive developer curriculum with thousands of hours of content, interactive coding challenges, and real-world projects.

Curriculum areas:

Responsive Web Design:
- HTML and CSS basics
- Applied accessibility
- Responsive web design principles
- CSS animation

JavaScript Algorithms and Data Structures:
- Basic JavaScript
- ES6 features
- Regular expressions
- Debugging
- Basic data structures
- Basic algorithm scripting
- Object-oriented programming
- Functional programming
- Algorithm scripting

Front End Development Libraries:
- Bootstrap
- jQuery
- Sass
- React
- Redux

Data Visualization:
- D3.js
- Data visualization projects

APIs and Microservices:
- Node.js and Express
- MongoDB and Mongoose
- Back end development projects

Quality Assurance:
- Chai testing framework
- Node.js testing
- Quality assurance projects

Scientific Computing with Python:
- Python basics
- Data analysis
- Python projects

Data Analysis with Python:
- NumPy
- Pandas
- Data analysis projects

Information Security:
- Helmet.js
- Security best practices

Machine Learning with Python:
- TensorFlow basics
- Machine learning projects

Each certification requires completing 5 required projects and passing all challenges. freeCodeCamp has helped millions of developers learn coding skills and find jobs in the tech industry.""",
        "source_name": "FreeCodeCamp",
        "source_category": "university_learning",
        "quality_score": 0.91,
    },
    {
        "url": "https://www.khanacademy.org/computing",
        "title": "Khan Academy Computing and Programming",
        "content_text": """Khan Academy Computing and Programming provides free computer science education for learners of all ages.

Courses include:

Intro to JS: Drawing & Animation
- Introduction to programming with JavaScript
- Drawing shapes and text
- Animations and interactive programs
- Variables and functions
- Simple games and simulations

Intro to HTML/CSS: Making webpages
- HTML fundamentals: elements, attributes, tables
- CSS fundamentals: selectors, properties, layouts
- Building interactive web pages
- Responsive design principles

Advanced JS: Games & Visualizations
- Object-oriented programming
- Advanced animation techniques
- Interactive data visualizations
- Game development

Intro to SQL: Querying and managing data
- Database fundamentals
- SQL queries and data manipulation
- Table creation and relationships
- Data analysis with SQL

AP Computer Science Principles
- The internet and networking
- Data and information
- Algorithms and programming
- Impact of computing
- Programming in JavaScript

HTML/CSS: Making webpages interactive
- DOM manipulation
- Event handling
- Interactive forms and interfaces

Advanced CSS: Visual effects
- Grid layouts
- Transforms and animations
- Custom properties
- Advanced selectors

All courses feature:
- Interactive coding environments
- Step-by-step tutorials
- Practice exercises
- Projects and challenges
- Community forums for support
- Progress tracking""",
        "source_name": "Khan Academy",
        "source_category": "university_learning",
        "quality_score": 0.93,
    },
    {
        "url": "https://www.seriouseats.com/",
        "title": "Serious Eats - Food Knowledge and Cooking",
        "content_text": """Serious Eats is the foremost site of food science and culture since 2006. We are the curious cooks, experts, journalists, and nerds behind Serious Eats, dedicated to bringing you the best food knowledge and techniques.

5x IACP Best Culinary Website
7,000+ Rigorously Tested Recipes
1K Gear Guides and Reviews
9M+ Monthly Readers

Recent highlights:
- Caesared Spaghetti: The main ingredients of Caesar salad reimagined in a savory pasta (15 mins)
- German Potato Salad: Tangy, smoky, and sweet potato salad for BBQ (25 mins)
- 3-Ingredient Frozen Whipped Lemonade: Frosty, fluffy lemon drink (25 mins)
- Cannoli Tart: All the flavors of cannoli in an easy tart (1 hr 30 mins)
- Midwestern Cream Slaw: Retro cream slaw that's brighter and tangier (20 mins)
- Blistered-Tomato Pasta Salad: Burst cherry tomatoes become a rich sauce (25 mins)

Food Science:
- Is It Safe to Store an Open Can in the Fridge?
- I Tested Fresh vs. Frozen Berries for Smoothies
- The Simple Trick That Keeps Shrimp Plump and Snappy

Equipment Reviews:
- The Best Vanilla Extracts for Baking
- The Top-Performing Dutch Ovens at Every Budget
- Great Pizza Steel for Home

World Cuisines:
- The Drink of the Gods: An Introduction to Pulque
- How to Stock a Moroccan Pantry: Harissa, Ras el Hanout, and More
- 10-Minute Stir-Fried Kashmiri Mushrooms

Serious Eats stands out for its rigorous testing methodology - every recipe is tested multiple times to ensure it works perfectly. The site combines food science explanations with practical cooking techniques, helping readers understand not just how but why cooking works.""",
        "source_name": "Serious Eats",
        "source_category": "knowledge_foundations",
        "quality_score": 0.90,
    },
    {
        "url": "https://imslp.org/wiki/Main_Page",
        "title": "IMSLP - International Music Score Library Project",
        "content_text": """Welcome to the International Music Score Library Project (IMSLP) / Petrucci Music Library! This site strives to share the world's public domain music.

Statistics:
- 261,651 works
- 27,940 composers
- 2,079 performers
- 883,319 scores
- 15,914,126+ pages
- 95,145 recordings

Recent News:
- 6 September 2026 - IMSLP now has 95,000 recordings
- 4 September 2026 - 261,000 works have scores or parts
- 31 August 2026 - 880,000 scores

Featured Works:
- Original manuscript of Heinichen's Ascolta Eurillo ascolta e datti pace, S.176
- First edition of Ignatius Sancho's 12 Country Dances
- First edition of Grandval's Villanelle
- First edition of Phalèse's Cantionum sacrarum, Liber 6 (1558)
- First edition of Bax's November Woods, GP 191

New Scores include works by Marchal, Zitterbart Jr., Galos, Smyth, Shostakovich, Caccini, Ketèlbey, Bliss, and Liszt.

New Recordings include works by Olvera, Bungert, Ferrari, Ponomareff, Rondeau, and others.

The IMSLP provides free access to scores and recordings in the public domain, making it one of the largest digital music libraries in the world. The site is available in 20 languages and serves musicians, scholars, and music lovers worldwide.

Content is available under the Creative Commons Attribution-ShareAlike 4.0 License.""",
        "source_name": "IMSLP",
        "source_category": "knowledge_foundations",
        "quality_score": 0.93,
    },
    {
        "url": "https://data.gov/",
        "title": "Data.gov - US Open Government Data",
        "content_text": """Data.gov is the home of the U.S. Government's Open Data. Here you will find data, tools, and resources to conduct research, develop web and mobile applications, design data visualizations, and more.

559,540 datasets available

Mission: The United States Government's open data site is designed to unleash the power of government open data to inform decisions by the public and policymakers, drive innovation and economic activity, achieve agency missions, and strengthen the foundation of an open and transparent government.

Data.gov provides access to:
- Most Viewed Datasets
- Recently Added Datasets
- Datasets by Organization
- Geospatial data
- Code Repository

Data Management and Governance:
- Data Tools
- Data Incubator Guidance
- Case Studies and Examples
- Skills Development

Federal Source Code Repositories:
The SHARE IT Act (Public Law 118-187) requires federal agencies to maintain source code repositories and make them publicly discoverable through Data.gov.

Data.gov also provides:
- Catalog data at catalog.data.gov
- Metrics and analytics on dataset usage
- Historical data on dataset age and distribution
- Top dataset page views by agency
- File download statistics
- External link click tracking

The platform serves researchers, developers, journalists, policy makers, and citizens who need access to government data for analysis, applications, and research.""",
        "source_name": "Data.gov",
        "source_category": "data_economics",
        "quality_score": 0.92,
    },
    {
        "url": "https://ourworldindata.org/",
        "title": "Our World in Data",
        "content_text": """Our World in Data provides research and data to make progress against the world's largest problems.

Key statistics:
- 14,095 charts
- 126 topic pages
- 29 data explorers
- 506 articles
- All free: open access and openly licensed

Popular pages:
- CO2 Emissions
- Population Growth
- Life Expectancy
- Poverty
- Impacts of Food
- War and Peace
- Global Education
- Artificial Intelligence
- Democracy

Recent articles and data insights:
- Is organic farming better for the environment than conventional farming?
- A never-ending childhood of malaria - many children experience five or more malaria infections a year
- Wild mammals have declined by 85% since the rise of humans
- How fast is the world building up solar power? Updated data from IRENA
- Tracking the size of public sector workforces around the world
- Hannah Ritchie's second book "Clearing the Air" is out in paperback

Data Insights:
- Beef production has been the largest driver of deforestation this century
- Haiti and the Dominican Republic share the last Caribbean island with endemic malaria
- Men are more likely to use tobacco than women almost everywhere
- Around 5% of electricity in the US is used for data centers
- China only just missed the income cutoff to become a high-income country
- In nine African countries, average incomes have more than doubled since 1990
- Five countries had more women than men in parliament in 2025

Featured data visualizations include child mortality rates, extreme poverty, life expectancy, CO2 emissions per capita, GDP per capita, undernourishment, literacy rates, and access to electricity.

Topics span population, health, energy, environment, food, poverty, education, innovation, human rights, democracy, and conflict.""",
        "source_name": "Our World in Data",
        "source_category": "data_economics",
        "quality_score": 0.95,
    },
    {
        "url": "https://www.epa.gov/",
        "title": "US Environmental Protection Agency",
        "content_text": """The U.S. Environmental Protection Agency (EPA) is an official government organization responsible for protecting human health and the environment.

The EPA works to ensure that:
- Air quality meets safe standards
- Water is clean and safe to drink
- Land is properly protected and remediated
- Chemicals and pesticides are safe
- Hazardous waste is properly managed
- Environmental justice is advanced

Key EPA programs and initiatives:

Clean Air: The EPA sets and enforces air quality standards to protect public health from air pollution, including regulations on greenhouse gases, particulate matter, and ozone.

Clean Water: Programs to protect water quality in rivers, lakes, streams, and groundwater, including the Clean Water Act enforcement.

Chemical Safety: Review and regulation of chemicals under the Toxic Substances Control Act (TSCA) and the Federal Insecticide, Fungicide, and Rodenticide Act (FIFRA).

Waste Management: Regulation of hazardous and non-hazardous waste, including Superfund cleanup of contaminated sites.

Climate Change: Research, data, and programs addressing climate change, including greenhouse gas reporting and clean energy initiatives.

Environmental Justice: Ensuring that all communities receive equal protection from environmental and health hazards.

Research and Science: EPA conducts and funds research on environmental and health topics, maintains environmental databases, and provides scientific assessments.

The EPA was established in 1970 and serves as the primary federal agency for environmental protection in the United States.""",
        "source_name": "EPA",
        "source_category": "science_research",
        "quality_score": 0.93,
    },
]

# ============================================================
# Process and index each item
# ============================================================

print(f"\n{'='*60}")
print(f"  Diverse Human Knowledge Indexer")
print(f"  Items to process: {len(items)}")
print(f"{'='*60}\n")

crawled = 0
indexed = 0
skipped_slop = 0
skipped_short = 0
categories = {}
sources_found = {}

for item in items:
    url = item["url"]
    title = item["title"]
    content = item["content_text"]
    source_name = item["source_name"]
    source_category = item["source_category"]
    quality_score = item["quality_score"]
    
    crawled += 1
    
    # Skip if content too short
    if len(content.strip()) < 50:
        print(f"  SKIP (too short): {title}")
        skipped_short += 1
        continue
    
    # Calculate word count
    word_count = len(content.split())
    
    # Truncate content to 5000 chars
    content_truncated = content[:5000].rsplit(" ", 1)[0] if len(content) > 5000 else content
    
    # Create knowledge item dict
    knowledge_item = {
        "url": url,
        "title": title,
        "content_text": content_truncated,
        "source_name": source_name,
        "source_category": source_category,
        "author": "",
        "published_date": "",
        "language": "en",
        "word_count": word_count,
        "quality_score": quality_score,
        "overall_rank": quality_score,
        "authority_score": quality_score,
        "freshness_score": 0.7,
        "engagement_score": 0.7,
        "content_hash": hashlib.md5(content_truncated.encode()).hexdigest(),
    }
    
    # Run SlopDetector
    slop_result = detector.analyze(content_truncated, source_name, title, url)
    if slop_result.is_slop:
        print(f"  SKIP (slop detected): {title} - confidence: {slop_result.confidence:.3f}")
        skipped_slop += 1
        continue
    
    # Run calculate_content_quality
    quality_metrics = calculate_content_quality(knowledge_item)
    
    # Index into database
    item_id = layer.upsert_knowledge_item(knowledge_item)
    if item_id:
        indexed += 1
        categories[source_category] = categories.get(source_category, 0) + 1
        sources_found[source_name] = sources_found.get(source_name, 0) + 1
        print(f"  INDEXED: {title} (id={item_id}, words={word_count}, quality={quality_score:.2f})")
    else:
        print(f"  FAILED: {title}")

# Refresh FTS and close
layer.refresh_fts()
layer.close()

# Final report
print(f"\n{'='*60}")
print(f"  CRAWL AND INDEX COMPLETE")
print(f"{'='*60}")
print(f"\n📊 Summary:")
print(f"   Total crawled: {crawled}")
print(f"   Total indexed: {indexed}")
print(f"   Skipped (slop): {skipped_slop}")
print(f"   Skipped (short): {skipped_short}")

print(f"\n📁 Categories:")
for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
    print(f"   {cat}: {count}")

print(f"\n🌐 Sources:")
for src, count in sorted(sources_found.items(), key=lambda x: -x[1]):
    print(f"   {src}: {count}")

# Verify DB state
import sqlite3
db_path = os.path.join(os.path.dirname(__file__), "search/data/local.db")
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM knowledge_items")
total = c.fetchone()[0]
c.execute("SELECT source_category, COUNT(*) FROM knowledge_items GROUP BY source_category")
cats = dict(c.fetchall())
c.execute("SELECT source_name, COUNT(*) FROM knowledge_items GROUP BY source_name")
srcs = dict(c.fetchall())
conn.close()

print(f"\n📦 Database State:")
print(f"   Total items in DB: {total}")
print(f"   Categories: {cats}")
print(f"   Sources: {srcs}")
print(f"\n{'='*60}")
