package tema2.t01;

import java.util.Scanner;

// Desarrolla un programa que lea números hasta que se den cinco ceros y escriba después la suma de los números leídos.
// Ejemplo:
// Introduciendo: 1 3 5 0 4 0 6 7 –1 0 0 8 0
// Da como salida: 33
public class ej23 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        int numero;
        int suma = 0;
        int contadorCeros = 0;

        System.out.println("Introduce numeros (el programa termina cuando se introduzcan 5 ceros):");

        while (contadorCeros < 5) {
            numero = sc.nextInt();

            if (numero == 0) {
                contadorCeros++;
            } else {
                suma += numero;
            }
        }

        System.out.println("La suma de los numeros leídos es: " + suma);
    }
}
